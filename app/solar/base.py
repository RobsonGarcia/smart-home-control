"""
A abstração de fontes de energia solar.

Cada fabricante de inversor tem uma API cloud própria, com autenticação,
nomes de campo e escalas diferentes. Este módulo define as duas coisas que
NÃO variam por fabricante:

  1. CANAIS_SOLAR — o vocabulário canônico do sistema. "Corrente do canal 1"
     tem UM código (`corrente_mppt_1`), um nome amigável e uma unidade,
     independente de quem fabricou o inversor. É o que torna uma série da
     marca X comparável com a da marca Y no mesmo grupo de energia.

  2. FonteSolar — a interface que cada driver implementa. Nenhuma
     nomenclatura de fabricante sai do driver: telemetria e canais chegam ao
     resto do app já traduzidos para os códigos canônicos e convertidos para
     a unidade real.

Fabricante novo = uma classe nova (driver) + registro em DRIVERS
(app/solar/__init__.py). Se ele medir algo que ainda não tem código
canônico, o código entra UMA vez aqui — e passa a existir para todos.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Dict, Iterator, List

from ..errors import ValidationError

# --------------------------------------------------------------------------
# Capacidades: o que um nível de acesso de um fabricante consegue fornecer.
# Um fabricante pode vender a mesma API em camadas (a AiSWEI tem "Business/
# Pro" e "User/comum") — o driver declara, POR NÍVEL, o que existe, e o resto
# do sistema (configurador, coletor, telas) se limita a isso.
#
#   plantas     listar as plantas da conta (descobrir_plantas)
#   inversores  listar os inversores de uma planta
#   telemetria  última leitura POR INVERSOR (ultima_telemetria)
#   canais      leitura por canal MPPT/string (descobrir_canais + valores)
#   historico   histórico paginado por inversor (backfill)
#   resumo      cartão de resumo da planta (resumo_planta)
# --------------------------------------------------------------------------
CAPACIDADES = ('plantas', 'inversores', 'telemetria', 'canais',
               'historico', 'resumo')


# --------------------------------------------------------------------------
# Vocabulário canônico. Mesmo formato de entrada do DPS_NOMEADOS
# (dps_mapping.py), que absorve esta tabela — os seletores de série e os
# gráficos resolvem nome/unidade sem saber que o canal é solar.
# --------------------------------------------------------------------------
CANAIS_SOLAR: Dict[str, dict] = {
    "potencia_ca": {"name": "Potência CA", "unit": "W", "type": "numeric"},
    "geracao_hoje": {"name": "Geração hoje", "unit": "kWh", "type": "numeric"},
    "geracao_total": {"name": "Geração total", "unit": "kWh", "type": "numeric"},
    "frequencia_ca": {"name": "Frequência da rede", "unit": "Hz", "type": "numeric"},
    "temperatura": {"name": "Temperatura do inversor", "unit": "°C", "type": "numeric"},
}

# Canais MPPT ("canal" na interface: é onde um conjunto de placas se conecta).
for _n in range(1, 7):
    CANAIS_SOLAR["potencia_mppt_%d" % _n] = {
        "name": "Potência — canal %d" % _n, "unit": "W", "type": "numeric"}
    CANAIS_SOLAR["tensao_mppt_%d" % _n] = {
        "name": "Tensão — canal %d" % _n, "unit": "V", "type": "numeric"}
    CANAIS_SOLAR["corrente_mppt_%d" % _n] = {
        "name": "Corrente — canal %d" % _n, "unit": "A", "type": "numeric"}

# Strings individuais (inversores maiores medem cada string separada).
for _n in range(1, 11):
    CANAIS_SOLAR["corrente_string_%d" % _n] = {
        "name": "Corrente — string %d" % _n, "unit": "A", "type": "numeric"}

# Lado CA, por fase.
for _n in range(1, 4):
    CANAIS_SOLAR["tensao_ca_%d" % _n] = {
        "name": "Tensão CA — fase %d" % _n, "unit": "V", "type": "numeric"}
    CANAIS_SOLAR["corrente_ca_%d" % _n] = {
        "name": "Corrente CA — fase %d" % _n, "unit": "A", "type": "numeric"}

del _n


# --------------------------------------------------------------------------
# O que trafega entre driver e aplicação. Dicionários seriam suficientes,
# mas as dataclasses documentam o contrato — e o contrato é o produto aqui.
# --------------------------------------------------------------------------
@dataclass
class Planta:
    apikey: str            # identificador da planta na cloud do fabricante
    nome: str
    potencia_kw: float     # potência instalada
    cidade: str = ""
    status: str = ""       # texto livre do driver ("normal", "offline"…)


@dataclass
class Inversor:
    sn: str                # número de série — identidade do equipamento
    estado: str = ""       # texto livre do driver
    ultima_comunicacao: str = ""
    psn: str = ""          # serial do coletor/PMU, quando existir


@dataclass
class Telemetria:
    tmstp_ms: int          # instante da MEDIÇÃO (epoch ms) — não o do polling
    valores: Dict[str, float] = field(default_factory=dict)  # código canônico -> valor
    online: bool = True


@dataclass
class ResumoPlanta:
    potencia_w: float = 0.0
    geracao_hoje_kwh: float = 0.0
    geracao_mes_kwh: float = 0.0
    geracao_total_kwh: float = 0.0
    ultima_atualizacao: str = ""
    status: str = ""


class FonteSolar(ABC):
    """
    Interface de um fabricante. O construtor recebe o dict de credenciais
    salvo na integração (mais `planta_apikey`, depois que a planta é
    escolhida). Erros de credencial/permissão viram ValidationError com
    mensagem legível; erros de rede podem propagar — o coletor os trata.
    """

    # Identificação para o registro e para o formulário do configurador.
    id: str = ""
    rotulo: str = ""
    # [{"chave", "rotulo", "secreto"}] — o formulário é montado a partir daqui,
    # então um fabricante com credenciais diferentes não muda o template.
    campos_credenciais: List[dict] = []
    # Níveis de acesso oferecidos pelo fabricante, do mais completo para o
    # mais restrito. Cada um: {valor, rotulo, descricao, disponivel,
    # capacidades}. `disponivel=False` = declarado mas ainda sem
    # implementação (aparece no formulário, desabilitado, com o motivo).
    niveis_acesso: List[dict] = [{
        "valor": "padrao", "rotulo": "Padrão", "descricao": "",
        "disponivel": True, "capacidades": set(CAPACIDADES),
    }]

    def __init__(self, credenciais: dict):
        self.credenciais = dict(credenciais or {})

    # -- nível de acesso e capacidades ---------------------------------

    def nivel_acesso(self) -> dict:
        """O nível configurado nesta instância (default: o primeiro)."""
        valor = (self.credenciais.get("nivel_acesso") or "").strip().lower()
        for nivel in self.niveis_acesso:
            if nivel["valor"] == valor:
                return nivel
        return self.niveis_acesso[0]

    def capacidades(self) -> set:
        return set(self.nivel_acesso().get("capacidades") or ())

    def tem(self, capacidade: str) -> bool:
        return capacidade in self.capacidades()

    def exigir_nivel_disponivel(self) -> None:
        """Barra o uso de um nível declarado mas ainda não implementado."""
        nivel = self.nivel_acesso()
        if not nivel.get("disponivel", True):
            raise ValidationError(
                "O nível de acesso '%s' de %s ainda não é suportado: %s"
                % (nivel["rotulo"], self.rotulo or self.id,
                   nivel.get("descricao") or "sem detalhes"))

    @abstractmethod
    def testar(self) -> None:
        """Valida as credenciais; levanta ValidationError com o motivo."""

    @abstractmethod
    def descobrir_plantas(self) -> List[Planta]:
        """As plantas visíveis para estas credenciais."""

    @abstractmethod
    def descobrir_inversores(self) -> List[Inversor]:
        """Os inversores da planta (`planta_apikey` nas credenciais)."""

    @abstractmethod
    def descobrir_canais(self, sn: str) -> List[dict]:
        """
        Os canais que ESTE inversor realmente tem, como
        [{"code", "name", "unit"}] com códigos canônicos — um inversor de
        2 canais não anuncia corrente_mppt_3.
        """

    @abstractmethod
    def ultima_telemetria(self, sn: str) -> Telemetria:
        """Última leitura do inversor, já canônica e em unidade real."""

    @abstractmethod
    def historico(self, sn: str, inicio_utc: str, fim_utc: str) -> Iterator[Telemetria]:
        """
        Leituras entre inicio e fim ("YYYY-MM-DD HH:MM:SS", UTC), paginando
        internamente. Alimenta o backfill.
        """

    @abstractmethod
    def resumo_planta(self) -> ResumoPlanta:
        """Cartão de resumo da planta (potência agora, geração hoje/mês/total)."""
