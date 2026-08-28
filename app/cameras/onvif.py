"""
Driver ONVIF — o caminho local para a imagem, sem nuvem e sem fabricante.

**Sem dependência nova, de propósito.** `onvif-zeep` arrastaria zeep + lxml +
o WSDL inteiro para fazer, aqui, quatro chamadas fixas: GetDeviceInformation,
GetCapabilities, GetProfiles e Get{Snapshot,Stream}Uri. São quatro envelopes
literais e um parser de nome local de tag — menos código do que a integração,
e coerente com um projeto que hoje tem só `requests` e `tinytuya`.

Duas particularidades de câmera barata que este arquivo já assume:

- **Autenticação dupla.** O padrão manda WS-Security UsernameToken (digest
  sha1 sobre nonce+created+senha). Muita câmera ignora isso e quer HTTP
  Digest no endpoint. Mandamos o cabeçalho WS-Security sempre e, num 401,
  repetimos com HTTP Digest.
- **XAddr do serviço de mídia.** O correto é perguntar em GetCapabilities;
  parte das câmeras responde tudo no mesmo endpoint do device_service. Se a
  capacidade não vier, usamos o próprio device_service.
"""

import base64
import hashlib
import logging
import os
import socket
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Dict, List, Optional
from urllib.parse import quote, urlparse, urlunparse

import requests
from requests.auth import HTTPBasicAuth, HTTPDigestAuth

from app.config import PORTAS_ONVIF
from app.errors import ValidationError
from .base import CAPACIDADES_VIDEO, CameraDescoberta, FonteVideo, Perfil

logger = logging.getLogger(__name__)

TIMEOUT_S = 6
MULTICAST = "239.255.255.250"
PORTA_DISCOVERY = 3702

_NS = (
    'xmlns:s="http://www.w3.org/2003/05/soap-envelope" '
    'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/'
    'oasis-200401-wss-wssecurity-secext-1.0.xsd" '
    'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/'
    'oasis-200401-wss-wssecurity-utility-1.0.xsd" '
    'xmlns:tds="http://www.onvif.org/ver10/device/wsdl" '
    'xmlns:trt="http://www.onvif.org/ver10/media/wsdl" '
    'xmlns:tt="http://www.onvif.org/ver10/schema"'
)

_PROBE = """<?xml version="1.0" encoding="UTF-8"?>
<e:Envelope xmlns:e="http://www.w3.org/2003/05/soap-envelope"
 xmlns:w="http://schemas.xmlsoap.org/ws/2004/08/addressing"
 xmlns:d="http://schemas.xmlsoap.org/ws/2005/04/discovery"
 xmlns:dn="http://www.onvif.org/ver10/network/wsdl">
 <e:Header>
  <w:MessageID>uuid:%s</w:MessageID>
  <w:To e:mustUnderstand="true">urn:schemas-xmlsoap-org:ws:2005:04:discovery</w:To>
  <w:Action e:mustUnderstand="true">http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe</w:Action>
 </e:Header>
 <e:Body><d:Probe><d:Types>dn:NetworkVideoTransmitter</d:Types></d:Probe></e:Body>
</e:Envelope>"""


# ---------------------------------------------------------------- XML cru

def _nome(elemento) -> str:
    """Nome local da tag, sem o namespace — é por ele que procuramos."""
    return elemento.tag.rsplit("}", 1)[-1]


def _todos(raiz, nome: str) -> list:
    return [e for e in raiz.iter() if _nome(e) == nome]


def _primeiro(raiz, nome: str):
    for elemento in raiz.iter():
        if _nome(elemento) == nome:
            return elemento
    return None


def _texto(raiz, nome: str, padrao: str = "") -> str:
    elemento = _primeiro(raiz, nome)
    return (elemento.text or "").strip() if elemento is not None else padrao


# ------------------------------------------------------------- descoberta

def _ips_locais() -> List[str]:
    """
    Os IPv4 desta máquina. O Probe sai de CADA interface: numa máquina com
    Wi-Fi, cabo e adaptador de VM, mandar só pela rota padrão é a razão mais
    comum de "a descoberta não achou nada" com a câmera ligada do lado.
    """
    ips = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None,
                                       socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))          # não envia nada
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    return sorted(i for i in ips if not i.startswith("127."))


def descobrir(timeout: float = 4.0) -> List[CameraDescoberta]:
    """
    WS-Discovery: pergunta na rede quem é câmera ONVIF.

    Não precisa de credencial — a resposta traz o endereço do serviço, e é com
    ele que o configurador continua. Câmera que não responde aqui pode mesmo
    assim falar ONVIF (há firmware com o discovery desligado); por isso o
    configurador aceita host digitado à mão.
    """
    achadas: Dict[str, CameraDescoberta] = {}
    mensagem = (_PROBE % uuid.uuid4()).encode("utf-8")

    for ip in _ips_locais() or [""]:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
            if ip:
                sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                                socket.inet_aton(ip))
                sock.bind((ip, 0))
            sock.settimeout(0.8)
            sock.sendto(mensagem, (MULTICAST, PORTA_DISCOVERY))

            fim = time.monotonic() + timeout / max(1, len(_ips_locais() or [1]))
            while time.monotonic() < fim:
                try:
                    dados, origem = sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    break
                camera = _ler_resposta_probe(dados, origem[0])
                if camera:
                    achadas[camera.host + ":" + str(camera.porta)] = camera
        except OSError as exc:
            logger.debug("discovery em %s falhou: %s", ip or "padrão", exc)
        finally:
            sock.close()

    return sorted(achadas.values(), key=lambda c: c.host)


def _ler_resposta_probe(dados: bytes, origem: str) -> Optional[CameraDescoberta]:
    try:
        raiz = ET.fromstring(dados.decode("utf-8", "replace"))
    except ET.ParseError:
        return None

    xaddrs = _texto(raiz, "XAddrs")
    endereco = (xaddrs.split() or [""])[0]
    partes = urlparse(endereco) if endereco else None
    host = (partes.hostname if partes else None) or origem
    porta = (partes.port if partes else None) or 80

    # Scopes trazem nome/hardware em forma de URL: onvif://www.onvif.org/name/Foo
    escopos = _texto(raiz, "Scopes")
    def _escopo(chave):
        for item in escopos.split():
            if "/%s/" % chave in item:
                return item.rsplit("/", 1)[-1].replace("%20", " ")
        return ""

    return CameraDescoberta(host=host, porta=porta, xaddr=endereco,
                            fabricante=_escopo("hardware"),
                            nome=_escopo("name"))


# ------------------------------------------------------------------ SOAP

def _cabecalho_seguranca(usuario: str, senha: str) -> str:
    """WS-Security UsernameToken com senha digerida — nunca em claro."""
    if not usuario:
        return ""
    nonce = os.urandom(16)
    criado = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = hashlib.sha1(nonce + criado.encode() + senha.encode()).digest()
    return (
        "<s:Header><wsse:Security><wsse:UsernameToken>"
        "<wsse:Username>%s</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">%s'
        "</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">%s'
        "</wsse:Nonce>"
        "<wsu:Created>%s</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security></s:Header>"
    ) % (usuario, base64.b64encode(digest).decode(),
         base64.b64encode(nonce).decode(), criado)


class OnvifDriver(FonteVideo):
    id = "onvif"
    rotulo = "ONVIF (rede local)"
    capacidades = tuple(CAPACIDADES_VIDEO)
    campos_credenciais = [
        {"chave": "host", "rotulo": "IP da câmera", "secreto": False,
         "dica": "o mesmo IP que aparece no inventário"},
        {"chave": "porta", "rotulo": "Porta ONVIF", "secreto": False,
         "opcional": True, "dica": "80 na maioria; 8000 e 2020 aparecem"},
        {"chave": "usuario", "rotulo": "Usuário", "secreto": False,
         "dica": "geralmente 'admin'"},
        {"chave": "senha", "rotulo": "Senha", "secreto": True,
         "dica": "a senha definida no app do fabricante, não a do Wi-Fi"},
    ]

    # -- endereços --------------------------------------------------------

    @property
    def host(self) -> str:
        return str(self.config.get("host") or "").strip()

    @property
    def porta(self) -> int:
        try:
            return int(self.config.get("porta") or 80)
        except (TypeError, ValueError):
            return 80

    @property
    def _usuario(self) -> str:
        return str(self.config.get("usuario") or "")

    @property
    def _senha(self) -> str:
        return str(self.config.get("senha") or "")

    def _device_service(self) -> str:
        if self.config.get("xaddr"):
            return str(self.config["xaddr"])
        return "http://%s:%d/onvif/device_service" % (self.host, self.porta)

    # -- transporte -------------------------------------------------------

    def _chamar(self, xaddr: str, corpo: str, acao: str):
        envelope = ('<?xml version="1.0" encoding="UTF-8"?>'
                    '<s:Envelope %s>%s<s:Body>%s</s:Body></s:Envelope>'
                    % (_NS, _cabecalho_seguranca(self._usuario, self._senha),
                       corpo))
        cabecalhos = {"Content-Type": "application/soap+xml; charset=utf-8"}

        try:
            resposta = requests.post(xaddr, data=envelope.encode("utf-8"),
                                     headers=cabecalhos, timeout=TIMEOUT_S)
            if resposta.status_code == 401 and self._usuario:
                # Câmera que ignora WS-Security e quer HTTP Digest.
                resposta = requests.post(
                    xaddr, data=envelope.encode("utf-8"), headers=cabecalhos,
                    timeout=TIMEOUT_S,
                    auth=HTTPDigestAuth(self._usuario, self._senha))
        except requests.RequestException as exc:
            raise ValidationError(
                "Não consegui falar com a câmera em %s: %s. Confira o IP e a "
                "porta ONVIF." % (xaddr, exc))

        if resposta.status_code == 401:
            raise ValidationError(
                "A câmera recusou o usuário/senha (%s). No app do fabricante, "
                "confira o usuário ONVIF — costuma ser 'admin' com uma senha "
                "própria, diferente da conta do aplicativo." % acao)
        if resposta.status_code >= 400:
            raise ValidationError(
                "A câmera respondeu HTTP %d em %s. Este endereço pode não ser "
                "o serviço ONVIF." % (resposta.status_code, acao))

        try:
            raiz = ET.fromstring(resposta.content)
        except ET.ParseError:
            raise ValidationError(
                "Resposta ilegível da câmera em %s — provavelmente não é uma "
                "porta ONVIF." % acao)

        falha = _primeiro(raiz, "Fault")
        if falha is not None:
            motivo = _texto(falha, "Text") or _texto(falha, "Value") or acao
            raise ValidationError("A câmera recusou %s: %s" % (acao, motivo))
        return raiz

    # -- serviços ---------------------------------------------------------

    def _media_xaddr(self) -> str:
        """Endereço do serviço de mídia, com o device_service como plano B."""
        if self.config.get("media_xaddr"):
            return str(self.config["media_xaddr"])
        try:
            raiz = self._chamar(
                self._device_service(),
                "<tds:GetCapabilities><tds:Category>Media</tds:Category>"
                "</tds:GetCapabilities>", "GetCapabilities")
        except ValidationError:
            return self._device_service()

        media = _primeiro(raiz, "Media")
        endereco = _texto(media, "XAddr") if media is not None else ""
        return endereco or self._device_service()

    # -- contrato ---------------------------------------------------------

    def testar(self) -> dict:
        if not self.host:
            raise ValidationError("Informe o IP da câmera.")
        raiz = self._chamar(self._device_service(),
                            "<tds:GetDeviceInformation/>",
                            "GetDeviceInformation")
        return {
            "fabricante": _texto(raiz, "Manufacturer"),
            "modelo": _texto(raiz, "Model"),
            "firmware": _texto(raiz, "FirmwareVersion"),
            "serie": _texto(raiz, "SerialNumber"),
        }

    def perfis(self) -> List[Perfil]:
        media = self._media_xaddr()
        raiz = self._chamar(media, "<trt:GetProfiles/>", "GetProfiles")

        saida = []
        for no in _todos(raiz, "Profiles"):
            token = no.attrib.get("token") or ""
            if not token:
                continue
            resolucao = _primeiro(no, "Resolution")
            largura = altura = None
            if resolucao is not None:
                try:
                    largura = int(_texto(resolucao, "Width") or 0) or None
                    altura = int(_texto(resolucao, "Height") or 0) or None
                except ValueError:
                    pass
            saida.append(Perfil(token=token, nome=_texto(no, "Name") or token,
                                largura=largura, altura=altura,
                                snapshot_uri=self._snapshot_uri(media, token),
                                stream_uri=self._stream_uri(media, token)))
        if not saida:
            raise ValidationError(
                "A câmera respondeu ONVIF, mas não declarou nenhum perfil de "
                "vídeo.")
        return saida

    def _snapshot_uri(self, media: str, token: str) -> str:
        try:
            raiz = self._chamar(
                media, "<trt:GetSnapshotUri><trt:ProfileToken>%s"
                       "</trt:ProfileToken></trt:GetSnapshotUri>" % token,
                "GetSnapshotUri")
            return _texto(raiz, "Uri")
        except ValidationError as exc:
            # Nem toda câmera tem snapshot; sem ele o painel tira o quadro do
            # próprio RTSP com o ffmpeg. Não é motivo para falhar a configuração.
            logger.info("câmera %s sem GetSnapshotUri: %s", self.host, exc.message)
            return ""

    def _stream_uri(self, media: str, token: str) -> str:
        raiz = self._chamar(
            media,
            "<trt:GetStreamUri><trt:StreamSetup>"
            "<tt:Stream>RTP-Unicast</tt:Stream>"
            "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport>"
            "</trt:StreamSetup><trt:ProfileToken>%s</trt:ProfileToken>"
            "</trt:GetStreamUri>" % token, "GetStreamUri")
        return _texto(raiz, "Uri")

    def snapshot(self) -> bytes:
        uri = str(self.config.get("snapshot_uri") or "")
        if not uri:
            raise ValidationError(
                "Esta câmera não expõe foto por ONVIF — o painel usa o quadro "
                "do próprio vídeo.")

        for auth in (HTTPDigestAuth(self._usuario, self._senha),
                     HTTPBasicAuth(self._usuario, self._senha)):
            try:
                resposta = requests.get(uri, auth=auth, timeout=TIMEOUT_S)
            except requests.RequestException as exc:
                raise ValidationError("Falha ao buscar a foto: %s" % exc)
            if resposta.status_code == 200 and resposta.content:
                return resposta.content
            if resposta.status_code != 401:
                break
        raise ValidationError(
            "A câmera não devolveu a foto (HTTP %d)." % resposta.status_code)

    def url_stream(self) -> str:
        uri = str(self.config.get("stream_uri") or "")
        if not uri:
            raise ValidationError("Esta câmera ainda não tem stream RTSP "
                                  "configurado.")
        return com_credenciais(uri, self._usuario, self._senha)


def com_credenciais(uri: str, usuario: str, senha: str) -> str:
    """
    Injeta usuário e senha na URL RTSP — é assim que o ffmpeg autentica.

    O resultado tem senha em texto: nunca vai para a tela, para o log nem para
    uma resposta de API.
    """
    if not usuario or "@" in urlparse(uri).netloc:
        return uri
    partes = urlparse(uri)
    porta = ":%d" % partes.port if partes.port else ""
    netloc = "%s:%s@%s%s" % (quote(usuario, safe=""), quote(senha, safe=""),
                             partes.hostname or "", porta)
    return urlunparse(partes._replace(netloc=netloc))


def portas_abertas(host: str, portas=PORTAS_ONVIF, timeout: float = 0.6):
    """Quais das portas típicas respondem — diagnóstico da sonda."""
    abertas = []
    for porta in portas:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            if s.connect_ex((host, int(porta))) == 0:
                abertas.append(int(porta))
    return abertas
