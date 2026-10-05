// Página /tv da TV: pergunta ao servidor se alguém já escolheu a tela desta TV e, quando
// escolher, vai direto para as propagandas. Se o código vencer, carrega um código novo.
"use strict";

(function () {
  const api = document.body.dataset.api;
  const aviso = document.getElementById("aviso");
  const inicio = Date.now();
  const validade = Number(document.body.dataset.validade || 600) * 1000;

  async function perguntar() {
    if (Date.now() - inicio > validade) { location.reload(); return; }
    try {
      const resposta = await fetch(api, { cache: "no-store" });
      if (resposta.status === 410) { location.reload(); return; }
      if (!resposta.ok) throw new Error("HTTP " + resposta.status);
      const dados = await resposta.json();
      if (dados.pronto) {
        aviso.textContent = "Conectada! Abrindo as propagandas…";
        location.href = dados.url;
        return;
      }
    } catch (erro) {
      aviso.textContent = "Sem conexão com o servidor. Tentando de novo…";
    }
    setTimeout(perguntar, 2000);
  }

  // Clique ou tecla F para tela cheia (a TV costuma ficar assim o tempo todo).
  function telaCheia() {
    if (!document.fullscreenElement) document.documentElement.requestFullscreen().catch(function () {});
  }
  document.addEventListener("click", telaCheia);
  document.addEventListener("keydown", function (e) { if (e.key === "f" || e.key === "F") telaCheia(); });
  perguntar();
})();
