// Tela de propagandas: seleção de várias propagandas e escolha de telas.
"use strict";

(function () {
  const formLote = document.getElementById("form-lote");
  const marcarTodas = document.getElementById("marcar-todas");
  const contador = document.getElementById("qtd-marcadas");

  // "Todas as telas" marcado: a lista de telas e grupos não vale, então fica apagada.
  document.querySelectorAll("[data-escolher-telas]").forEach(function (bloco) {
    const lista = bloco.querySelector(".lista-telas");
    function atualizar() {
      const todas = bloco.querySelector('input[name="destino"][value="todas"]').checked;
      lista.classList.toggle("apagada", todas);
    }
    bloco.querySelectorAll('input[name="destino"]').forEach(function (r) { r.addEventListener("change", atualizar); });
    atualizar();
  });

  if (!formLote) return;
  const caixas = Array.from(document.querySelectorAll('input[name="ids"][form="form-lote"]'));
  const botoes = Array.from(formLote.querySelectorAll('button[type="submit"]'));
  const paineis = Array.from(formLote.querySelectorAll("details"));

  function atualizarSelecao() {
    const marcadas = caixas.filter(function (c) { return c.checked; }).length;
    contador.textContent = marcadas;
    formLote.classList.toggle("com-selecao", marcadas > 0);
    formLote.classList.toggle("sem-selecao", marcadas === 0);
    if (marcadas === 0) paineis.forEach(function (p) { p.open = false; });
    botoes.forEach(function (b) { b.disabled = marcadas === 0; });
    caixas.forEach(function (c) { c.closest(".prop").classList.toggle("marcada", c.checked); });
    if (marcarTodas) {
      marcarTodas.checked = marcadas === caixas.length;
      marcarTodas.indeterminate = marcadas > 0 && marcadas < caixas.length;
    }
  }

  caixas.forEach(function (c) { c.addEventListener("change", atualizarSelecao); });
  if (marcarTodas) {
    marcarTodas.addEventListener("change", function () {
      caixas.forEach(function (c) { c.checked = marcarTodas.checked; });
      atualizarSelecao();
    });
  }

  // Só um painel (telas ou tempo) aberto por vez.
  paineis.forEach(function (painel) {
    painel.addEventListener("toggle", function () {
      if (painel.open) paineis.forEach(function (outro) { if (outro !== painel) outro.open = false; });
    });
  });

  formLote.addEventListener("submit", function (evento) {
    const botao = evento.submitter;
    const marcadas = caixas.filter(function (c) { return c.checked; }).length;
    if (botao && botao.value === "excluir" &&
        !confirm("Excluir " + marcadas + " propaganda(s)? Os arquivos serão apagados.")) {
      evento.preventDefault();
    }
  });

  atualizarSelecao();
})();
