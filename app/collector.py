import json
import logging
import time
import tinytuya
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import (
    SYNC_MONITOR_CONFIGS_INTERVAL_SECONDS,
    DEFAULT_POLL_INTERVAL_SECONDS
)
from app.repository import (
    get_all_monitor_configs,
    get_device,
    insert_reading
)

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
            insert_reading(device_id, json.dumps(status_bruto['dps']), True)
        else:
            logger.warning(f"   ❌ {name}: Dispositivo não respondeu usando "
                          f"protocolo {version}.")
            insert_reading(device_id, "{}", False)

    except Exception as e:
        logger.error(f"   💥 Erro ao ler {name}: {str(e)}")
        insert_reading(device_id, "{}", False)


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

        if device_id in scheduled_jobs:
            # Job já existe; se intervalo mudou, reagendar
            old_job = scheduled_jobs[device_id]
            if old_job.trigger.interval.total_seconds() != poll_interval:
                try:
                    scheduler.remove_job(old_job.id)
                    logger.info(f"Job reendar para device {device_id}: "
                               f"{poll_interval}s")
                    job = scheduler.add_job(
                        collect_device_status,
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
                    collect_device_status,
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
