"use strict";

const tela = document.getElementById("tela");
const aviso = document.getElementById("aviso");
const letreiro = document.getElementById("letreiro");

const URL_PLAYLIST = document.body.dataset.api;
const URL_PULSO = document.body.dataset.pulso;   // vazio no player geral (/player)
const CHAVE_PENDENTES = "exibicoes-pendentes:" + URL_PULSO;
const MAX_PENDENTES = 20000;                     // ~2 dias de exibições sem rede
const INTERVALO_PULSO = 30 * 1000;   // sinal de vida (o painel considera offline após 75 s sem sinal)
const INICIO = Date.now();
const UM_DIA = 24 * 60 * 60 * 1000;

let itens = [];        // última lista recebida (usada se o servidor cair)
let pausado = false;   // a loja pausou todas as propagandas no painel
let letreiroGeral = "";  // o da loja (ou o da tela); cada propaganda pode ter o próprio
let posicao = -1;
let textoLetreiro = null;
let temporizador = null;
let rodada = 0;        // evita que duas trocas aconteçam ao mesmo tempo
let atual = null;      // { id, inicio } da propaganda na tela
const preCarregados = new Set();

// ---------------------------------------------------------------------------
// Playlist
// ---------------------------------------------------------------------------

// Busca a lista atualizada antes de cada propaganda. Assim, mudanças
// feitas no painel aparecem sem precisar recarregar a tela.
async function atualizarLista() {
  try {
    const resposta = await fetch(URL_PLAYLIST, { cache: "no-store" });
    if (resposta.status === 403) {
      // Este aparelho não está (mais) conectado à tela: volta para a página do QR code.
      const dados = await resposta.json().catch(() => ({}));
      location.href = dados.conectar || "/tela";
      return false;
    }
    if (!resposta.ok) throw new Error("HTTP " + resposta.status);
    const dados = await resposta.json();
    itens = dados.itens;
    pausado = Boolean(dados.pausado);
    letreiroGeral = dados.letreiro || "";
    if (pausado || itens.length === 0) mostrarLetreiro(letreiroGeral);
    preCarregar(itens);
    return true;
  } catch (erro) {
    console.warn("Sem conexão com o servidor, usando a lista anterior.", erro);
    return false;
  }
}

// Baixa as mídias com antecedência (uma de cada vez) para a troca ser
// instantânea e para continuar exibindo se a rede cair.
let filaPreCarga = Promise.resolve();
function preCarregar(lista) {
  for (const item of lista) {
    if (preCarregados.has(item.url)) continue;
    preCarregados.add(item.url);
    filaPreCarga = filaPreCarga
      .then(() => fetch(item.url).then(r => r.blob()))
      .catch(() => preCarregados.delete(item.url));
  }
}

function mostrarLetreiro(texto) {
  if (texto === textoLetreiro) return;
  textoLetreiro = texto;
  const span = letreiro.querySelector("span");
  span.textContent = texto;
  letreiro.style.display = texto ? "block" : "none";
  // Velocidade constante, independente do tamanho do texto.
  span.style.animationDuration = Math.max(10, texto.length * 0.25) + "s";
}

// ---------------------------------------------------------------------------
// Registro de exibições (relatórios) e sinal de vida (monitoramento)
// ---------------------------------------------------------------------------

function lerPendentes() {
  try {
    return JSON.parse(localStorage.getItem(CHAVE_PENDENTES)) || [];
  } catch (erro) {
    return [];
  }
}

function gravarPendentes(lista) {
  try {
    localStorage.setItem(CHAVE_PENDENTES, JSON.stringify(lista.slice(-MAX_PENDENTES)));
  } catch (erro) {
    console.warn("Não foi possível guardar as exibições pendentes.", erro);
  }
}

function encerrarExibicaoAtual() {
  if (!atual || !URL_PULSO) return;
  const duracao = (Date.now() - atual.inicio) / 1000;
  if (duracao >= 1) {
    const pendentes = lerPendentes();
    pendentes.push({ propaganda_id: atual.id, inicio: new Date(atual.inicio).toISOString(), duracao: duracao });
    gravarPendentes(pendentes);
  }
  atual = null;
}

async function enviarPulso() {
  if (!URL_PULSO) return;
  const lote = lerPendentes().slice(0, 1000);
  try {
    const resposta = await fetch(URL_PULSO, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ exibindo: atual ? atual.id : null, exibicoes: lote }),
    });
    if (!resposta.ok) throw new Error("HTTP " + resposta.status);
    // Remove só o que foi enviado (novas exibições podem ter entrado enquanto isso).
    gravarPendentes(lerPendentes().slice(lote.length));
  } catch (erro) {
    console.warn("Pulso não enviado; tentará de novo.", erro);
  }
}

// ---------------------------------------------------------------------------
// Exibição
// ---------------------------------------------------------------------------

async function proxima() {
  clearTimeout(temporizador);
  encerrarExibicaoAtual();
  const minha = ++rodada;
  const seguir = () => { if (minha === rodada) proxima(); };
  const conectado = await atualizarLista();
  if (minha !== rodada) return;
  // Telas que ficam ligadas por semanas: recarrega a página uma vez por dia
  // (só quando o servidor responde) para receber atualizações e liberar memória.
  if (conectado && Date.now() - INICIO > UM_DIA) {
    await enviarPulso();
    location.reload();
    return;
  }

  if (pausado) {
    // Pausa pelo painel: tela preta, sem aviso, até alguém retomar.
    tela.innerHTML = "";
    aviso.style.display = "none";
    temporizador = setTimeout(seguir, 10000);
    return;
  }
  if (itens.length === 0) {
    tela.innerHTML = "";
    aviso.textContent = "Nenhuma propaganda no ar nesta tela.";
    aviso.style.display = "flex";
    temporizador = setTimeout(seguir, 10000);
    return;
  }
  aviso.style.display = "none";

  posicao = (posicao + 1) % itens.length;
  const item = itens[posicao];

  tela.style.opacity = 0;
  await new Promise(r => setTimeout(r, 600));
  if (minha !== rodada) return;
  tela.innerHTML = "";
  // Letreiro próprio da propaganda ("" = sem letreiro) ou, se não tiver, o geral.
  mostrarLetreiro(item.letreiro === null || item.letreiro === undefined ? letreiroGeral : item.letreiro);

  const comecou = () => { if (minha === rodada) atual = { id: item.id, inicio: Date.now() }; };
  const falhou = () => { atual = null; seguir(); };   // não conta como exibida

  if (item.tipo === "video") {
    const video = document.createElement("video");
    video.src = item.url;
    video.autoplay = true;
    video.playsInline = true;
    video.onplaying = () => { if (!atual) comecou(); };
    video.onended = seguir;
    video.onerror = falhou;
    tela.appendChild(video);
    // Tenta tocar com som; se o navegador bloquear, toca sem som.
    video.play().catch(() => { video.muted = true; video.play().catch(falhou); });
    // Segurança: se o vídeo travar, pula após 10 minutos.
    temporizador = setTimeout(seguir, 10 * 60 * 1000);
  } else {
    const img = document.createElement("img");
    img.onload = comecou;
    img.onerror = falhou;
    img.src = item.url;
    tela.appendChild(img);
    temporizador = setTimeout(seguir, item.duracao * 1000);
  }
  tela.style.opacity = 1;
}

// Clique ou tecla F para tela cheia.
function telaCheia() {
  if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(() => {});
}
document.addEventListener("click", telaCheia);
document.addEventListener("keydown", e => { if (e.key === "f" || e.key === "F") telaCheia(); });

// Enquanto uma propaganda está na tela (um vídeo pode ser longo), confere a cada
// 15 segundos se a loja pausou, para a TV parar sem esperar a propaganda acabar.
setInterval(async () => {
  if (pausado || !atual) return;
  const rodadaAtual = rodada;
  if (await atualizarLista() && pausado && rodadaAtual === rodada) proxima();
}, 15000);

if (URL_PULSO) {
  enviarPulso();
  setInterval(enviarPulso, INTERVALO_PULSO);
  // Guarda a exibição em andamento se a página for fechada ou recarregada, e avisa o
  // servidor que a janela fechou (a tela aparece offline na hora, no painel).
  window.addEventListener("pagehide", () => {
    encerrarExibicaoAtual();
    if (navigator.sendBeacon) {
      navigator.sendBeacon(URL_PULSO, new Blob([JSON.stringify({ saindo: true })], { type: "application/json" }));
    }
  });
}
proxima();
