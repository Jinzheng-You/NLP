const els = {
  text: document.getElementById("textInput"),
  apiKey: document.getElementById("apiKeyInput"),
  provider: document.getElementById("providerSelect"),
  translate: document.getElementById("translateBtn"),
  regen: document.getElementById("regenBtn"),
  clear: document.getElementById("clearBtn"),
  toggleKey: document.getElementById("toggleKeyBtn"),
  copy: document.getElementById("copyBtn"),
  loading: document.getElementById("loadingBox"),
  loadingText: document.getElementById("loadingText"),
  original: document.getElementById("originalText"),
  labels: document.getElementById("labelSequence"),
  visualScroll: document.getElementById("visualScroll"),
  matched: document.getElementById("matchedCount"),
  missing: document.getElementById("missingCount"),
  totalLabels: document.getElementById("totalLabels"),
  totalImages: document.getElementById("totalImages"),
  heroLabel: document.getElementById("heroLabelCount"),
  heroImage: document.getElementById("heroImageCount"),
  heroMapped: document.getElementById("heroMappedCount"),
  explanation: document.getElementById("explanationText"),
  modal: document.getElementById("previewModal"),
  previewImage: document.getElementById("previewImage"),
  previewLabel: document.getElementById("previewLabel"),
  closeModal: document.getElementById("closeModalBtn"),
  toast: document.getElementById("toast"),
};

const loadingLines = [
  "正在解析语义核心...",
  "正在匹配地书符号矩阵...",
  "正在生成视觉符号长卷...",
  "正在校准古文字与 AI 的共振频率...",
];
let loadingTimer = null;
let latestResult = null;

function toast(message) {
  els.toast.textContent = message;
  els.toast.classList.remove("hidden");
  setTimeout(() => els.toast.classList.add("hidden"), 2600);
}

async function loadStats() {
  const res = await fetch("/api/translator/stats");
  const stats = await res.json();
  els.totalLabels.textContent = stats.label_count ?? 0;
  els.totalImages.textContent = stats.image_count ?? 0;
  if (els.heroLabel) els.heroLabel.textContent = stats.label_count ?? 0;
  if (els.heroImage) els.heroImage.textContent = stats.image_count ?? 0;
  if (els.heroMapped) els.heroMapped.textContent = stats.mapped_count ?? 0;
}

function setLoading(active) {
  els.translate.disabled = active;
  els.regen.disabled = active;
  els.loading.classList.toggle("hidden", !active);
  if (active) {
    let i = 0;
    els.translate.textContent = "生成中...";
    els.loadingText.textContent = loadingLines[0];
    loadingTimer = setInterval(() => {
      i = (i + 1) % loadingLines.length;
      els.loadingText.textContent = loadingLines[i];
    }, 900);
  } else {
    els.translate.textContent = "转换为地书语言";
    clearInterval(loadingTimer);
  }
}

async function translate() {
  const text = els.text.value.trim();
  if (!text) {
    toast("请输入一段自然语言。");
    if (window.soundManager) window.soundManager.playFalse();
    return;
  }
  setLoading(true);
  try {
    const res = await fetch("/api/translator/translate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        text,
        api_key: els.apiKey.value.trim(),
        provider: els.provider.value,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.success) throw new Error(data.error || "转换失败");
    latestResult = data;
    renderResult(data);
    if (window.soundManager) window.soundManager.playTrue();
    if (window.playDishuBarrage) {
      const barrageSources = (data.items || [])
        .filter((item) => item.type !== "punctuation" && item.matched && item.image_url)
        .map((item) => item.image_url);
      window.playDishuBarrage(barrageSources, { count: Math.max(28, Math.min(48, barrageSources.length * 5)) });
    }
    if (data.warning) toast(data.warning);
  } catch (err) {
    toast(err.message || "请求失败");
    if (window.soundManager) window.soundManager.playFalse();
  } finally {
    setLoading(false);
  }
}

function renderResult(data) {
  els.original.textContent = data.original_text || "";
  els.labels.textContent = (data.dishu_labels || []).join("  /  ") || "无标签";
  els.explanation.textContent = data.explanation || "";
  els.matched.textContent = data.stats?.matched ?? 0;
  els.missing.textContent = data.stats?.missing ?? 0;
  els.totalLabels.textContent = data.stats?.label_library_total ?? els.totalLabels.textContent;
  els.totalImages.textContent = data.stats?.image_library_total ?? els.totalImages.textContent;

  els.visualScroll.innerHTML = "";
  (data.items || []).forEach((item, index) => {
    const card = document.createElement("figure");
    card.className = "translator-symbol-card";
    card.style.animationDelay = `${index * 80}ms`;

    if (item.type === "punctuation") {
      card.classList.add("punctuation-card");
      const punctuation = document.createElement("div");
      punctuation.className = "punctuation-symbol";
      punctuation.textContent = item.label;
      card.appendChild(punctuation);
    } else if (item.matched && item.image_url) {
      const img = document.createElement("img");
      img.src = item.image_url;
      img.alt = item.label;
      img.loading = "lazy";
      img.addEventListener("click", () => openPreview(item));
      card.appendChild(img);
    } else {
      const missing = document.createElement("div");
      missing.className = "missing-symbol";
      missing.textContent = "?";
      card.appendChild(missing);
    }

    const caption = document.createElement("figcaption");
    caption.textContent = item.type === "punctuation" ? "标点" : item.label || item.requested_label || "未命名";
    card.appendChild(caption);
    els.visualScroll.appendChild(card);
  });

  if (!data.items?.length) {
    els.visualScroll.innerHTML = '<div class="empty-state">没有生成可展示的符号</div>';
  }
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function openPreview(item) {
  els.previewImage.src = item.image_url;
  els.previewLabel.textContent = item.label;
  els.modal.classList.remove("hidden");
}

function clearResult() {
  latestResult = null;
  els.original.textContent = "等待输入";
  els.labels.textContent = "尚未生成";
  els.explanation.textContent = "等待输入。";
  els.matched.textContent = "0";
  els.missing.textContent = "0";
  els.visualScroll.innerHTML = '<div class="empty-state">等待符号长卷生成</div>';
}

els.translate.addEventListener("click", translate);
els.regen.addEventListener("click", translate);
els.clear.addEventListener("click", () => {
  els.text.value = "";
  clearResult();
});
els.toggleKey.addEventListener("click", () => {
  const visible = els.apiKey.type === "text";
  els.apiKey.type = visible ? "password" : "text";
  els.toggleKey.textContent = visible ? "显示" : "隐藏";
});
els.copy.addEventListener("click", async () => {
  const text = latestResult?.dishu_labels?.join(", ") || "";
  if (!text) {
    toast("暂无可复制的标签序列。");
    if (window.soundManager) window.soundManager.playFalse();
    return;
  }
  await navigator.clipboard.writeText(text);
  toast("标签序列已复制。");
  if (window.soundManager) window.soundManager.playTrue();
});
els.closeModal.addEventListener("click", () => els.modal.classList.add("hidden"));
els.modal.querySelector(".translator-modal-backdrop").addEventListener("click", () => els.modal.classList.add("hidden"));

loadStats().catch(() => toast("统计信息加载失败。"));
