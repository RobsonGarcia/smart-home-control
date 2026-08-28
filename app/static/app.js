/* ==========================================================================
   Painel Tuya — camada compartilhada.
   Antes disto cada template inventava o seu próprio fetch, o seu próprio
   aviso de erro e o seu próprio window.onclick de modal. Está tudo aqui.
   ========================================================================== */

/* ------------------------------------------------------------------ API */

/**
 * Chama a API e devolve o JSON. Em erro, lança com a mensagem do servidor.
 *
 * O backend responde erro como {"detail": "..."} com o status certo. Antes as
 * rotas devolviam `return {...}, 400`, que virava HTTP 200 com um array — e o
 * `if (!response.ok)` do front nunca disparava, então erro passava por sucesso.
 */
async function api(metodo, url, corpo) {
    const opcoes = { method: metodo, headers: {} };
    if (corpo !== undefined) {
        opcoes.headers['Content-Type'] = 'application/json';
        opcoes.body = JSON.stringify(corpo);
    }

    let resposta;
    try {
        resposta = await fetch(url, opcoes);
    } catch (erro) {
        throw new Error('Não foi possível falar com o servidor: ' + erro.message);
    }

    let dados = null;
    const texto = await resposta.text();
    if (texto) {
        try { dados = JSON.parse(texto); } catch (e) { dados = { detail: texto }; }
    }

    if (!resposta.ok) {
        const msg = (dados && (dados.detail || dados.error))
            || ('Erro ' + resposta.status);
        const erro = new Error(msg);
        erro.status = resposta.status;
        erro.dados = dados;
        throw erro;
    }
    return dados;
}

const apiGet = (url) => api('GET', url);
const apiPost = (url, corpo) => api('POST', url, corpo);
const apiPut = (url, corpo) => api('PUT', url, corpo);
const apiDelete = (url) => api('DELETE', url);

/* ---------------------------------------------------------------- toasts */

function toast(mensagem, tipo) {
    let pilha = document.getElementById('toast-stack');
    if (!pilha) {
        pilha = document.createElement('div');
        pilha.id = 'toast-stack';
        pilha.className = 'toast-stack';
        document.body.appendChild(pilha);
    }

    const el = document.createElement('div');
    el.className = 'toast ' + (tipo || 'info');
    const marca = document.createElement('span');
    marca.className = 'marca';
    const texto = document.createElement('span');
    texto.textContent = mensagem;
    el.appendChild(marca);
    el.appendChild(texto);
    pilha.appendChild(el);

    // Erro fica mais tempo na tela: costuma ter algo para ler.
    setTimeout(() => el.remove(), tipo === 'erro' ? 7000 : 4000);
}

const toastOk = (m) => toast(m, 'ok');
const toastErro = (m) => toast(m, 'erro');

/** Envolve uma ação de API: mostra o erro em vez de deixá-lo passar batido. */
async function executar(acao, mensagemOk, aoTerminar) {
    try {
        const r = await acao();
        if (mensagemOk) toastOk(mensagemOk);
        if (aoTerminar) aoTerminar(r);
        return r;
    } catch (erro) {
        toastErro(erro.message);
        return null;
    }
}

function recarregar(atraso) {
    setTimeout(() => location.reload(), atraso || 700);
}

/* ---------------------------------------------------------------- modais */

function abrirModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.add('is-open');
}

function fecharModal(id) {
    const m = document.getElementById(id);
    if (m) m.classList.remove('is-open');
}

function fecharTodosModais() {
    document.querySelectorAll('.modal.is-open').forEach((m) => m.classList.remove('is-open'));
}

// Um listener só, delegado — antes duas páginas disputavam window.onclick.
document.addEventListener('click', (ev) => {
    if (ev.target.classList && ev.target.classList.contains('modal')) {
        ev.target.classList.remove('is-open');
    }
});
document.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') fecharTodosModais();
});

/* ------------------------------------------------------------- diálogos */

/*
 * confirm()/prompt() nativos destoam do painel, travam a página e nem
 * aparecem em alguns navegadores embutidos. Estes dois usam o MESMO modal
 * das telas e devolvem Promises:
 *
 *   if (!(await confirmar('Excluir o grupo?', { perigo: true }))) return;
 *   const nome = await pedirTexto('Novo nome do local:', { valor: atual });
 *
 * Cancelar (botão, Esc ou clique fora) resolve false/null — os handlers
 * globais de modal continuam valendo.
 */

function _dialogo() {
    let m = document.getElementById('dialogoPainel');
    if (m) return m;
    m = document.createElement('div');
    m.id = 'dialogoPainel';
    m.className = 'modal';
    m.innerHTML =
        '<div class="modal-content" style="max-width: 460px;">' +
        '  <div class="modal-head">' +
        '    <div><h2 data-d="titulo"></h2><div class="sub" data-d="mensagem"></div></div>' +
        '    <span class="close" data-d="cancelar">&times;</span>' +
        '  </div>' +
        '  <div class="modal-body" data-d="corpo" style="display: none;">' +
        '    <div class="campo"><input type="text" data-d="entrada" autocomplete="off"></div>' +
        '  </div>' +
        '  <div class="modal-foot">' +
        '    <button type="button" class="btn" data-d="cancelar">Cancelar</button>' +
        '    <button type="button" class="btn btn-primary" data-d="ok"></button>' +
        '  </div>' +
        '</div>';
    document.body.appendChild(m);
    return m;
}

function _abrirDialogo(mensagem, opcoes, comEntrada) {
    const o = opcoes || {};
    const m = _dialogo();
    const el = (papel) => m.querySelector('[data-d="' + papel + '"]');

    el('titulo').textContent = o.titulo || (comEntrada ? 'Informe' : 'Confirmar');
    el('mensagem').textContent = mensagem || '';
    el('corpo').style.display = comEntrada ? '' : 'none';

    const ok = el('ok');
    ok.textContent = o.rotulo || (comEntrada ? 'Salvar' : 'Confirmar');
    ok.classList.toggle('btn-danger', !!o.perigo);
    ok.classList.toggle('btn-primary', !o.perigo);

    const entrada = el('entrada');
    entrada.value = o.valor || '';
    entrada.placeholder = o.placeholder || '';

    m.classList.add('is-open');
    (comEntrada ? entrada : ok).focus();
    if (comEntrada) entrada.select();

    return new Promise((resolve) => {
        const cancelado = comEntrada ? null : false;
        let vivo = true;
        const fim = (valor) => {
            if (!vivo) return;
            vivo = false;
            m.classList.remove('is-open');
            m.removeEventListener('click', aoClicar);
            document.removeEventListener('keydown', aoTeclar, true);
            resolve(valor);
        };
        const aoClicar = (ev) => {
            const papel = ev.target.dataset && ev.target.dataset.d;
            if (papel === 'ok') {
                fim(comEntrada ? entrada.value.trim() : true);
            } else if (papel === 'cancelar' || ev.target === m) {
                fim(cancelado);
            }
        };
        const aoTeclar = (ev) => {
            if (ev.key === 'Escape') fim(cancelado);
            if (ev.key === 'Enter' && (!comEntrada || ev.target === entrada)) {
                ev.preventDefault();
                fim(comEntrada ? entrada.value.trim() : true);
            }
        };
        m.addEventListener('click', aoClicar);
        // capture: chega antes do fechador global de Esc, que só remove a
        // classe — sem isto a Promise ficaria pendurada para sempre.
        document.addEventListener('keydown', aoTeclar, true);
    });
}

function confirmar(mensagem, opcoes) {
    return _abrirDialogo(mensagem, opcoes, false);
}

function pedirTexto(mensagem, opcoes) {
    return _abrirDialogo(mensagem, opcoes, true);
}

/* ----------------------------------------------------------- acionamento */

/*
 * Mandar um comando a um aparelho. UMA função para as três telas (lista,
 * cômodos, detalhe) e para os controles de câmera — o que muda é só o botão.
 *
 * A confirmação sai do próprio botão (data-confirmar), que veio da coluna
 * confirmar_acao do dispositivo: quem decide se pergunta é o servidor, não o
 * JavaScript. E a recusa por falta de opt-in também é do servidor — este
 * botão nem aparece sem ela, mas se aparecesse a rota recusaria igual.
 */

function rotuloValor(valor) {
    if (valor === true) return 'ligado';
    if (valor === false) return 'desligado';
    return String(valor);
}

async function acionar(el, deviceId, dp, valor, opcoes) {
    const o = opcoes || {};
    const dados = (el && el.dataset) || {};

    if (dados.confirmar === '1') {
        const acao = dados.acao || 'este comando';
        const alvo = dados.nome ? ' em ' + dados.nome : '';
        const rotulo = valor === false ? 'Desligar'
                     : (valor === true ? 'Ligar' : 'Aplicar');
        const ok = await confirmar(rotulo + ' "' + acao + '"' + alvo + '?', {
            titulo: 'Confirmar acionamento', rotulo: rotulo, perigo: true,
        });
        if (!ok) return false;
    }

    if (el) el.disabled = true;
    try {
        const r = await apiPost('/devices/' + deviceId + '/comando',
                                { dp: dp, valor: valor });
        // "confirmado" = o aparelho devolveu o estado novo. Sem isso o comando
        // saiu, mas ninguém garante que ele obedeceu — e a tela diz isso.
        toastOk((r.nome || 'Comando') + ': ' + rotuloValor(r.valor)
                + (r.confirmado ? '' : ' (sem confirmação do aparelho)'));
        if (o.aoConcluir) o.aoConcluir(r);
        if (o.recarregar !== false) recarregar(900);
        return true;
    } catch (erro) {
        toastErro(erro.message);
        return false;
    } finally {
        if (el) el.disabled = false;
    }
}

/* ------------------------------------------------------- cores de séries */

/**
 * Paleta categórica de 8 slots, ordem fixa. Validada contra a superfície
 * #0f141b: banda de luminosidade, piso de croma, separação para daltonismo
 * (pior par adjacente ΔE 8,4) e contraste >= 3:1.
 */
const CORES_SERIE = [
    '#3987e5', '#d95926', '#199e70', '#c98500',
    '#d55181', '#008300', '#9085e9', '#e66767'
];

const COR_EXCEDENTE = '#6b7889';

/**
 * Atribui cor POR SÉRIE, a partir de uma chave estável, e não pela posição no
 * array. É o que faz esconder ou filtrar uma série não repintar as outras —
 * com `idx % cores.length` quem ficava mudava de cor a cada filtro.
 *
 * Passando de 8 séries as excedentes ficam cinza: a regra é nunca ciclar a
 * paleta, porque a 9ª cor seria indistinguível da 1ª sob daltonismo.
 */
function coresDeSeries(itens, chaveDe) {
    const chave = chaveDe || ((x) => x.id);
    const ordenadas = itens.slice().sort((a, b) => {
        const ka = chave(a), kb = chave(b);
        return ka < kb ? -1 : (ka > kb ? 1 : 0);
    });
    const mapa = {};
    ordenadas.forEach((item, i) => {
        mapa[chave(item)] = i < CORES_SERIE.length ? CORES_SERIE[i] : COR_EXCEDENTE;
    });
    mapa.__excedentes = Math.max(0, ordenadas.length - CORES_SERIE.length);
    return mapa;
}

/* --------------------------------------------------------------- gráfico */

const TINTA = {
    texto: '#cbd5e2',
    fraca: '#6b7889',
    grade: 'rgba(255,255,255,0.06)',
    eixo: 'rgba(255,255,255,0.12)',
    superficie: '#0f141b',
    fundoTooltip: '#05070a'
};

/**
 * Config base do gráfico de linha com eixo de tempo.
 *
 * `parser` é obrigatório: collected_at vem do CURRENT_TIMESTAMP do SQLite como
 * "YYYY-MM-DD HH:MM:SS" — com espaço, não ISO com T — e o adapter não parseia
 * isso sozinho. E o adapter em si (chartjs-adapter-date-fns) precisa estar
 * carregado: o Chart.js 3 removeu o de fábrica, e sem ele qualquer eixo
 * `type: 'time'` lança em vez de desenhar.
 */
function configGraficoLinha(datasets, opcoes) {
    const o = opcoes || {};
    return {
        type: 'line',
        data: { datasets: datasets },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            scales: {
                x: {
                    type: 'time',
                    time: {
                        parser: 'yyyy-MM-dd HH:mm:ss',
                        tooltipFormat: 'dd/MM HH:mm',
                        displayFormats: {
                            minute: 'HH:mm', hour: 'HH:mm',
                            day: 'dd/MM', month: 'MM/yyyy'
                        }
                    },
                    grid: { color: TINTA.grade, drawBorder: false },
                    border: { color: TINTA.eixo },
                    ticks: {
                        color: TINTA.fraca,
                        font: { family: 'IBM Plex Mono, monospace', size: 11 },
                        maxRotation: 0,
                        autoSkipPadding: 20
                    }
                },
                y: {
                    beginAtZero: o.beginAtZero !== false,
                    grid: { color: TINTA.grade, drawBorder: false },
                    border: { display: false },
                    ticks: {
                        color: TINTA.fraca,
                        font: { family: 'IBM Plex Mono, monospace', size: 11 }
                    },
                    title: o.unidade ? {
                        display: true,
                        text: o.unidade,
                        color: TINTA.fraca,
                        font: { size: 11 }
                    } : undefined
                }
            },
            plugins: {
                // A legenda do Chart.js fica desligada: cada página desenha a
                // sua, em HTML, com o valor atual ao lado do nome.
                legend: { display: false },
                tooltip: {
                    backgroundColor: TINTA.fundoTooltip,
                    borderColor: 'rgba(255,255,255,0.14)',
                    borderWidth: 1,
                    titleColor: TINTA.fraca,
                    bodyColor: '#f2f6fb',
                    titleFont: { family: 'IBM Plex Mono, monospace', size: 11 },
                    bodyFont: { family: 'IBM Plex Sans, sans-serif', size: 12 },
                    padding: 12,
                    boxPadding: 5,
                    displayColors: true
                }
            }
        }
    };
}

/** Marca fina, ponto discreto, sem preenchimento — o traçado é o dado. */
// collected_at vem do banco em UTC sem sufixo ("YYYY-MM-DD HH:MM:SS") — o
// adapter de datas trataria como hora LOCAL e a curva inteira deslocaria o
// fuso (3 h no Brasil). A conversão acontece aqui, no único ponto por onde
// todo dataset passa.
function dataUtc(timestamp) {
    return new Date(timestamp.replace(' ', 'T') + 'Z');
}

function datasetDeSerie(rotulo, pontos, cor) {
    return {
        label: rotulo,
        data: pontos.map((p) => (
            typeof p.x === 'string' ? { x: dataUtc(p.x), y: p.y } : p
        )),
        borderColor: cor,
        backgroundColor: cor,
        borderWidth: 2,
        pointRadius: 0,
        pointHoverRadius: 4,
        pointHoverBorderWidth: 2,
        pointHoverBorderColor: TINTA.superficie,
        tension: 0.15,
        fill: false,
        spanGaps: false
    };
}

/* ------------------------------------------------------------- formatação */

function numero(v, casas) {
    if (v === null || v === undefined || isNaN(v)) return '—';
    return Number(v).toFixed(casas === undefined ? 0 : casas).replace('.', ',');
}

function haQuantoTempo(iso) {
    if (!iso) return 'nunca';
    // collected_at chega em UTC, sem sufixo de fuso: o Z evita o navegador
    // interpretar como hora local e mostrar diferença de fuso inteira.
    const t = new Date(iso.replace(' ', 'T') + (/[Zz+]/.test(iso) ? '' : 'Z'));
    const seg = Math.floor((Date.now() - t.getTime()) / 1000);
    if (!isFinite(seg)) return 'nunca';
    if (seg < 60) return 'há ' + Math.max(seg, 0) + ' s';
    if (seg < 3600) return 'há ' + Math.floor(seg / 60) + ' min';
    if (seg < 86400) return 'há ' + Math.floor(seg / 3600) + ' h';
    return 'há ' + Math.floor(seg / 86400) + ' dias';
}

/** Preenche todo [data-tempo] com o "há X" correspondente. */
function pintarTempos() {
    document.querySelectorAll('[data-tempo]').forEach((el) => {
        el.textContent = haQuantoTempo(el.getAttribute('data-tempo'));
    });
}

document.addEventListener('DOMContentLoaded', pintarTempos);
setInterval(pintarTempos, 30000);
