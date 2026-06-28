document.addEventListener("DOMContentLoaded", () => {
    initFloatingDishuBackground();

    const setupSection = document.querySelector("#game-setup.is-open");
    if (setupSection) {
        setupSection.scrollIntoView({ behavior: "smooth", block: "start" });
    }

    document.querySelectorAll(".option-form").forEach((form) => {
        form.addEventListener("submit", () => {
            const button = form.querySelector("button");
            if (button) {
                button.disabled = true;
                button.textContent = "提交中...";
            }
        });
    });

    const liveFilter = document.querySelector("[data-live-filter]");
    if (liveFilter) {
        const searchInput = liveFilter.querySelector('input[type="search"]');
        const categorySelect = liveFilter.querySelector("select");
        let timer = null;

        const submitSoon = () => {
            window.clearTimeout(timer);
            timer = window.setTimeout(() => liveFilter.requestSubmit(), 350);
        };

        if (searchInput) {
            searchInput.addEventListener("input", submitSoon);
        }
        if (categorySelect) {
            categorySelect.addEventListener("change", () => liveFilter.requestSubmit());
        }
    }

    triggerAnsweredDishuBarrage();
});

window.playDishuBarrage = function playDishuBarrage(sources, options = {}) {
    const validSources = (sources || [])
        .map((source) => (typeof source === "string" ? source : source?.src || source?.image_url || ""))
        .filter(Boolean);
    if (!validSources.length) return;

    let layer = document.querySelector("[data-dishu-barrage]");
    if (!layer) {
        layer = document.createElement("div");
        layer.className = "dishu-barrage-layer";
        layer.dataset.dishuBarrage = "true";
        document.body.appendChild(layer);
    }

    const count = options.count || Math.max(18, Math.min(42, validSources.length * 4));
    const burst = Array.from({ length: count }, () => validSources[Math.floor(Math.random() * validSources.length)]);
    burst.sort(() => Math.random() - 0.5);
    burst.forEach((src, index) => {
        window.setTimeout(() => createDishuBarrageItem(layer, src), index * (70 + Math.random() * 80));
    });
};

function createDishuBarrageItem(layer, src) {
    const wrap = document.createElement("div");
    const img = document.createElement("img");
    const fromLeft = Math.random() > 0.5;
    const rowTop = 18 + Math.random() * 68;
    const size = 42 + Math.random() * 28;
    const duration = 5200 + Math.random() * 2200;
    const drift = (Math.random() - 0.5) * 70;
    const rotation = (Math.random() - 0.5) * 34;

    wrap.className = `dishu-barrage-item ${fromLeft ? "from-left" : "from-right"}`;
    wrap.style.setProperty("--top", `${rowTop}vh`);
    wrap.style.setProperty("--size", `${size}px`);
    wrap.style.setProperty("--duration", `${duration}ms`);
    wrap.style.setProperty("--drift", `${drift}px`);
    wrap.style.setProperty("--drift-neg", `${drift * -0.35}px`);
    wrap.style.setProperty("--drift-soft", `${drift * 0.25}px`);
    wrap.style.setProperty("--rotation", `${rotation}deg`);
    wrap.style.setProperty("--rotation-neg", `${rotation * -1}deg`);
    wrap.style.setProperty("--offset", `${(size + 40) * -1}px`);
    wrap.style.setProperty("--delay", `${Math.random() * 120}ms`);

    img.src = src;
    img.alt = "";
    img.loading = "lazy";
    wrap.appendChild(img);
    layer.appendChild(wrap);

    window.setTimeout(() => wrap.remove(), duration + 900);
}

function triggerAnsweredDishuBarrage() {
    const feedback = document.querySelector(".feedback");
    if (!feedback) return;
    const sources = Array.from(document.querySelectorAll(".symbol-tile img")).map((img) => img.src);
    window.setTimeout(() => window.playDishuBarrage(sources, { count: 26 }), 220);
}

function initFloatingDishuBackground() {
    const layer = document.querySelector("[data-floating-dishu]");
    if (!layer || layer.dataset.initialized === "true") return;
    layer.dataset.initialized = "true";

    const nodes = Array.from(layer.querySelectorAll("img"));
    if (!nodes.length) return;

    for (let i = nodes.length - 1; i > 0; i -= 1) {
        const j = Math.floor(Math.random() * (i + 1));
        [nodes[i].src, nodes[j].src] = [nodes[j].src, nodes[i].src];
    }

    const pointer = {
        x: window.innerWidth / 2,
        y: window.innerHeight / 2,
        active: false,
        lastMove: 0,
    };
    let width = window.innerWidth;
    let height = window.innerHeight;

    const items = nodes.map((node) => {
        const size = 22 + Math.random() * 46;
        return {
            node,
            x: Math.random() * width,
            y: Math.random() * height,
            vx: (Math.random() - 0.5) * 0.24,
            vy: (Math.random() - 0.5) * 0.24,
            rotation: Math.random() * 360,
            spin: (Math.random() - 0.5) * 0.05,
            size,
            scale: 0.52 + Math.random() * 0.8,
            wander: Math.random() * Math.PI * 2,
        };
    });

    function resize() {
        width = window.innerWidth;
        height = window.innerHeight;
    }

    function tick() {
        const now = performance.now();
        const pointerIsFresh = pointer.active && now - pointer.lastMove < 1400;

        items.forEach((item, index) => {
            item.wander += 0.006 + index * 0.00008;
            item.vx += Math.cos(item.wander) * 0.004;
            item.vy += Math.sin(item.wander * 1.13) * 0.004;

            const dx = pointer.x - item.x;
            const dy = pointer.y - item.y;
            const dist = Math.max(Math.sqrt(dx * dx + dy * dy), 1);
            if (pointerIsFresh && dist < 260) {
                const pull = (1 - dist / 260) * 0.024;
                item.vx += (dx / dist) * pull;
                item.vy += (dy / dist) * pull;
            } else if (dist < 520) {
                const push = (1 - dist / 520) * 0.015;
                item.vx -= (dx / dist) * push;
                item.vy -= (dy / dist) * push;
            }

            for (let j = index + 1; j < items.length; j += 1) {
                const other = items[j];
                const apartX = item.x - other.x;
                const apartY = item.y - other.y;
                const apartDist = Math.max(Math.sqrt(apartX * apartX + apartY * apartY), 1);
                if (apartDist < 92) {
                    const spread = (1 - apartDist / 92) * 0.012;
                    const sx = (apartX / apartDist) * spread;
                    const sy = (apartY / apartDist) * spread;
                    item.vx += sx;
                    item.vy += sy;
                    other.vx -= sx;
                    other.vy -= sy;
                }
            }

            const speed = Math.sqrt(item.vx * item.vx + item.vy * item.vy);
            const maxSpeed = 1.05;
            if (speed > maxSpeed) {
                item.vx = (item.vx / speed) * maxSpeed;
                item.vy = (item.vy / speed) * maxSpeed;
            }

            item.x += item.vx;
            item.y += item.vy;
            item.rotation += item.spin;
            item.vx *= 0.992;
            item.vy *= 0.992;

            if (item.x < -80) item.x = width + 80;
            if (item.x > width + 80) item.x = -80;
            if (item.y < -80) item.y = height + 80;
            if (item.y > height + 80) item.y = -80;

            item.node.style.width = `${item.size}px`;
            item.node.style.height = `${item.size}px`;
            item.node.style.transform = `translate3d(${item.x}px, ${item.y}px, 0) rotate(${item.rotation}deg) scale(${item.scale})`;
        });
        requestAnimationFrame(tick);
    }

    window.addEventListener("resize", resize);
    window.addEventListener("mousemove", (event) => {
        pointer.x = event.clientX;
        pointer.y = event.clientY;
        pointer.active = true;
        pointer.lastMove = performance.now();
    });
    window.addEventListener("mouseleave", () => {
        pointer.active = false;
    });

    tick();
}
