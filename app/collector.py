import json
import logging
import time
from datetime import datetime, timedelta, timezone

import tinytuya
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import (
    SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS,
    SOLAR_BACKFILL_DIAS
)
from app.escala import aplicar_no_device
from app.repository import (
    get_all_monitor_configs,
    get_config_coleta_solar,
    get_device,
    get_integracoes_backfill_pendente,
    get_solar_inversores,
    insert_reading,
    marcar_backfill_feito,
    tmstps_do_device,
    ultima_leitura_tmstp
)
from app.solar import get_driver

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Estado global: rastreia jobs agendados por device_id
scheduled_jobs = {}
scheduler = BlockingScheduler()


def collect_device_status(device_id: str) -> None:
    """
    Coleta status de um device via tinytuya.
    Padrão reaproveitado de coletar.py:48-77
    Grava resultado em readings (online=True/False).
    """
    device = get_device(device_id)
    if not device:
        logger.warning(f"Device {device_id} não encontrado no inventário.")
        return

    name = device['name']
    ip = device['ip']
    dev_id = device['id']
    key = device['local_key']
    version = device['protocol_version'] or 3.4

    if not ip or ip == '0.0.0.0':
        logger.warning(f"❌ {name}: Sem endereço IP local mapeado.")
        insert_reading(device_id, "{}", False)
        return

    logger.info(f"📡 Conectando a: {name} ({ip}) usando Protocolo {version}...")

    try:
        dispositivo = tinytuya.OutletDevice(dev_id, ip, key)
        dispositivo.set_version(version)
        dispositivo.set_socketTimeout(3)
        dispositivo.set_socketPersistent(True)

        status_bruto = dispositivo.status()

        if status_bruto and 'dps' in status_bruto:
            logger.info(f"   ✅ {name}: Dados coletados com sucesso!")
            # O aparelho manda inteiro deslocado (1265 para 126,5 V); o `scale`
            # do mapping desfaz isso AQUI, para que readings guarde unidade
            # real e nenhuma tela precise lembrar de dividir.
            dps = aplicar_no_device(status_bruto['dps'], device)
            insert_reading(device_id, json.dumps(dps), True)
        else:
            logger.warning(f"   ❌ {name}: Dispositivo não respondeu usando "
                          f"protocolo {version}.")
            insert_reading(device_id, "{}", False)

    except Exception as e:
        logger.error(f"   💥 Erro ao ler {name}: {str(e)}")
        insert_reading(device_id, "{}", False)


def _tmstp_para_utc(tmstp_ms: int) -> str:
    """Epoch ms -> "YYYY-MM-DD HH:MM:SS" UTC, o formato do CURRENT_TIMESTAMP."""
    return datetime.fromtimestamp(tmstp_ms / 1000.0,
                                  tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _driver_solar(cfg: dict):
    """Instancia o driver a partir do que get_config_coleta_solar devolve."""
    credenciais = json.loads(cfg["credenciais_json"])
    credenciais["planta_apikey"] = cfg.get("planta_apikey") or ""
    credenciais["nivel_acesso"] = cfg.get("nivel_acesso") or ""
    return get_driver(cfg["driver"])(credenciais)


def collect_solar_status(device_id: str) -> None:
    """
    Coleta um inversor solar via driver cloud (contraparte de
    collect_device_status para source='solar').

    Diferenças deliberadas em relação ao Tuya: a leitura é gravada com o
    collected_at do EQUIPAMENTO (tmstp), não do polling — e uma telemetria
    com o mesmo tmstp da última gravada é pulada, porque o inversor mede a
    cada ~5 min e o polling pode bater duas vezes na mesma medição.
    """
    cfg = get_config_coleta_solar(device_id)
    if not cfg:
        logger.warning(f"Inversor {device_id} sem integração solar no banco.")
        return

    try:
        driver = _driver_solar(cfg)
        if not driver.tem("telemetria"):
            # Nível de acesso sem leitura por inversor: não é falha do
            # aparelho, então nada de gravar leitura offline.
            logger.debug(f"Inversor {cfg['sn']}: nível de acesso sem "
                         f"telemetria por inversor, coleta pulada.")
            return
        telemetria = driver.ultima_telemetria(cfg["sn"])
    except Exception as e:
        # Nunca incluir credenciais na mensagem — só o erro do driver.
        logger.error(f"   💥 Erro ao ler inversor {cfg['sn']}: {e}")
        insert_reading(device_id, "{}", False)
        return

    if not telemetria.online or not telemetria.tmstp_ms:
        logger.warning(f"   ❌ Inversor {cfg['sn']}: sem telemetria na cloud.")
        insert_reading(device_id, "{}", False)
        return

    if ultima_leitura_tmstp(device_id) == telemetria.tmstp_ms:
        logger.debug(f"Inversor {cfg['sn']}: mesma medição de antes, pulando.")
        return

    valores = dict(telemetria.valores)
    valores["tmstp"] = telemetria.tmstp_ms
    insert_reading(device_id, json.dumps(valores), True,
                   collected_at=_tmstp_para_utc(telemetria.tmstp_ms))
    logger.info(f"   ✅ Inversor {cfg['sn']}: telemetria de "
                f"{_tmstp_para_utc(telemetria.tmstp_ms)} gravada.")


_backfill_em_andamento = False


def backfill_solar() -> None:
    """
    Importa o histórico (SOLAR_BACKFILL_DIAS) das integrações pendentes.

    Roda AQUI, no coletor, porque leva minutos — o configurador web só cria a
    integração e responde. Só marca backfill_feito no sucesso; numa falha no
    meio, a próxima sincronização tenta de novo e tmstps_do_device impede
    pontos duplicados.
    """
    global _backfill_em_andamento
    if _backfill_em_andamento:
        return
    _backfill_em_andamento = True
    try:
        for integracao in get_integracoes_backfill_pendente():
            try:
                driver = _driver_solar(integracao)
                if not driver.tem("historico"):
                    # Nível sem histórico: não há o que importar — marca
                    # como feito para o job não renascer a cada sync.
                    marcar_backfill_feito(integracao["id"])
                    logger.info(f"Backfill de '{integracao['nome']}' pulado: "
                                f"nível de acesso sem histórico.")
                    continue
                fim = datetime.now(timezone.utc)
                inicio = fim - timedelta(days=SOLAR_BACKFILL_DIAS)
                fmt = "%Y-%m-%d %H:%M:%S"
                total = 0
                for inv in get_solar_inversores(integracao["id"]):
                    vistos = tmstps_do_device(inv["device_id"])
                    for t in driver.historico(inv["sn"], inicio.strftime(fmt),
                                              fim.strftime(fmt)):
                        if not t.tmstp_ms or t.tmstp_ms in vistos:
                            continue
                        vistos.add(t.tmstp_ms)
                        valores = dict(t.valores)
                        valores["tmstp"] = t.tmstp_ms
                        insert_reading(inv["device_id"], json.dumps(valores),
                                       True,
                                       collected_at=_tmstp_para_utc(t.tmstp_ms))
                        total += 1
                marcar_backfill_feito(integracao["id"])
                logger.info(f"Backfill de '{integracao['nome']}' concluído: "
                            f"{total} leituras importadas.")
            except Exception as e:
                logger.error(f"Backfill da integração {integracao['id']} "
                             f"falhou (tentará de novo): {e}")
    finally:
        _backfill_em_andamento = False


def sync_monitor_configs() -> None:
    """
    Sincroniza jobs agendados com base em monitor_configs do banco.
    Chamada a cada SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS.
    """
    logger.debug("Sincronizando configurações de monitoramento...")

    configs = get_all_monitor_configs()
    active_device_ids = {cfg['device_id'] for cfg in configs}

    # Remove jobs para devices não mais monitorados
    for device_id in list(scheduled_jobs.keys()):
        if device_id not in active_device_ids:
            try:
                job = scheduled_jobs[device_id]
                scheduler.remove_job(job.id)
                del scheduled_jobs[device_id]
                logger.info(f"Job removido para device {device_id}")
            except Exception as e:
                logger.error(f"Erro ao remover job de {device_id}: {e}")

    # Adiciona/atualiza jobs para devices monitorados
    for cfg in configs:
        device_id = cfg['device_id']
        poll_interval = cfg['poll_interval_seconds']
        # O despacho por tipo de fonte acontece aqui, no único lugar em que
        # jobs nascem: inversor solar coleta via driver cloud, o resto via
        # tinytuya na LAN.
        funcao_coleta = (collect_solar_status if cfg.get('source') == 'solar'
                         else collect_device_status)

        if device_id in scheduled_jobs:
            # Job já existe; se intervalo mudou, reagendar
            old_job = scheduled_jobs[device_id]
            if old_job.trigger.interval.total_seconds() != poll_interval:
                try:
                    scheduler.remove_job(old_job.id)
                    logger.info(f"Job reendar para device {device_id}: "
                               f"{poll_interval}s")
                    job = scheduler.add_job(
                        funcao_coleta,
                        IntervalTrigger(seconds=poll_interval),
                        args=[device_id],
                        id=f"job_{device_id}",
                        replace_existing=True,
                        max_instances=1
                    )
                    scheduled_jobs[device_id] = job
                except Exception as e:
                    logger.error(f"Erro ao reagendar job de {device_id}: {e}")
        else:
            # Novo job
            try:
                job = scheduler.add_job(
                    funcao_coleta,
                    IntervalTrigger(seconds=poll_interval),
                    args=[device_id],
                    id=f"job_{device_id}",
                    max_instances=1
                )
                scheduled_jobs[device_id] = job
                logger.info(f"Job agendado para device {device_id}: "
                           f"{poll_interval}s")
            except Exception as e:
                logger.error(f"Erro ao agendar job de {device_id}: {e}")

    # Backfill pendente vira um job único. A flag interna de backfill_solar
    # impede execução dupla se o sync rodar de novo antes de ele terminar.
    if get_integracoes_backfill_pendente():
        try:
            scheduler.add_job(backfill_solar, id="backfill_solar",
                              replace_existing=True, max_instances=1)
        except Exception as e:
            logger.error(f"Erro ao agendar backfill solar: {e}")

    logger.debug(f"Sincronização concluída. Jobs ativos: {len(scheduled_jobs)}")


def run_collector() -> None:
    """
    Inicia o collector como processo contínuo.
    Configura um job de sincronização e o BlockingScheduler é iniciado.
    """
    logger.info("Iniciando collector...")

    # Sincroniza imediatamente
    sync_monitor_configs()

    # Agenda sincronização periódica
    scheduler.add_job(
        sync_monitor_configs,
        IntervalTrigger(seconds=SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS),
        id="sync_configs",
        max_instances=1
    )

    logger.info(f"Sincronização de configs agendada a cada "
               f"{SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS}s")

    try:
        scheduler.start()
    except KeyboardInterrupt:
        logger.info("Collector interrompido pelo usuário.")
        scheduler.shutdown()


if __name__ == "__main__":
    run_collector()
