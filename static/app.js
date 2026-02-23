(function () {
  function inferBasePathFromLocation() {
    try {
      const p = String(window.location.pathname || '');
      if (!p) return '';
      if (p.includes('/s/')) return p.split('/s/')[0];
      if (p.includes('/api/')) return p.split('/api/')[0];
      if (p.endsWith('/index.html')) return p.slice(0, -'/index.html'.length);
      // If served as /<repo>/ (directory index), keep without trailing slash
      if (p.endsWith('/')) return p.replace(/\/$/, '');
      return '';
    } catch (e) {
      return '';
    }
  }

  const explicitBasePath = (window.__BASE_PATH__ || '').replace(/\/$/, '');
  const basePath = (explicitBasePath || inferBasePathFromLocation()).replace(/\/$/, '');

  function getTheme() {
    return localStorage.getItem('theme') || 'light';
  }

  function setTheme(theme) {
    document.documentElement.setAttribute('data-bs-theme', theme);
    localStorage.setItem('theme', theme);
  }

  function initThemeToggle() {
    setTheme(getTheme());
    const btn = document.getElementById('themeToggle');
    if (!btn) return;
    btn.textContent = getTheme() === 'dark' ? '亮色模式' : '暗色模式';
    btn.addEventListener('click', () => {
      const next = getTheme() === 'dark' ? 'light' : 'dark';
      setTheme(next);
      btn.textContent = next === 'dark' ? '亮色模式' : '暗色模式';
    });
  }

  async function fetchJson(url) {
    const resp = await fetch(url, { cache: 'no-store' });
    if (!resp.ok) throw new Error('fetch failed');
    return await resp.json();
  }

  function sentimentText(v) {
    if (v > 0.2) return '多头情绪占优';
    if (v < -0.2) return '空头情绪占优';
    return '中性';
  }

  function fmt2(v) {
    if (typeof v !== 'number' || !isFinite(v)) return '--';
    return v.toFixed(2);
  }

  function renderSummary(day) {
    const el = document.getElementById('summaryCard');
    if (!el) return;
    const s = day.sentiment.index;
    const band = day.sentiment.band;
    const price = day.price || {};
    const priceStatus = price.status || 'ok';
    const priceUnavailable = priceStatus !== 'ok';
    const stale = !priceUnavailable && !!(day.is_stale || (day.price && day.price.is_stale));
    const priceAsOf = (!priceUnavailable && price.date) ? String(price.date) : '';
    const staleNote = stale && priceAsOf && priceAsOf !== String(day.date)
      ? `<div class="text-body-secondary">休市/无当日成交数据：使用最近交易日 ${priceAsOf}</div>`
      : '';
    const priceNote = priceUnavailable
      ? `<div class="text-body-secondary">价格数据不可用：${price.reason || priceStatus}</div>`
      : '';
    const closeText = priceUnavailable ? '--' : fmt2(price.close);
    const pctText = priceUnavailable ? '--' : `${fmt2(price.pct_change)}%`;
    const volText = priceUnavailable ? '--' : (price.volume ?? '--');
    el.innerHTML = `
      <div class="d-flex flex-wrap justify-content-between gap-3">
        <div>
          <div class="text-body-secondary">今日情绪指数</div>
          <div class="fs-2 fw-semibold sentiment-${band}">${fmt2(s)} <span class="fs-5 fw-normal">(${sentimentText(s)})</span></div>
          <div class="text-body-secondary">更新：${day.updated_at}</div>
          ${staleNote}
          ${priceNote}
        </div>
        <div>
          <div class="text-body-secondary">收盘价</div>
          <div class="fs-4 fw-semibold">${closeText}</div>
          <div class="text-body-secondary">涨跌幅：${pctText} · 成交量：${volText}</div>
        </div>
      </div>
    `;
  }

  function renderNews(day) {
    const list = document.getElementById('newsList');
    const empty = document.getElementById('newsEmpty');
    if (!list || !empty) return;
    list.innerHTML = '';
    const items = (day.news || []).slice(0);
    if (items.length === 0) {
      empty.classList.remove('d-none');
      return;
    }
    empty.classList.add('d-none');
    for (const it of items) {
      const emo = it.sentiment === 'bull' ? '📈' : it.sentiment === 'bear' ? '📉' : '⚖️';
      const scope = it.scope === 'macro' ? '宏观' : it.scope === 'symbol' ? '品种' : '';
      const scopeBadge = scope ? `<span class="badge text-bg-secondary ms-2">${scope}</span>` : '';
      const aStart = it.url ? `<a href="${it.url}" target="_blank" rel="noopener noreferrer">` : '<span>';
      const aEnd = it.url ? '</a>' : '</span>';
      const div = document.createElement('div');
      div.className = 'news-item';
      div.innerHTML = `
        <div class="fw-semibold">${emo} ${aStart}${it.title}${aEnd}${scopeBadge}</div>
        <div class="news-meta">来源：${it.source || '--'} · ${it.published_at || ''} · 置信度 ${fmt2(it.confidence)}</div>
      `;
      list.appendChild(div);
    }
  }

  function renderAgents(day) {
    const box = document.getElementById('agentsBox');
    const empty = document.getElementById('agentsEmpty');
    if (!box || !empty) return;
    box.innerHTML = '';

    const agents = day.agents;
    if (!agents) {
      empty.classList.remove('d-none');
      return;
    }
    empty.classList.add('d-none');

    function line(title, obj) {
      if (!obj) return;
      const status = obj.status || 'unknown';
      const div = document.createElement('div');
      div.className = 'd-flex flex-wrap justify-content-between gap-2';

      if (status !== 'ok') {
        div.innerHTML = `<div class="fw-semibold">${title}</div><div class="text-body-secondary">${obj.reason || status}</div>`;
        box.appendChild(div);
        return;
      }

      const idx = typeof obj.index === 'number' ? obj.index : 0;
      const band = obj.band || 'neutral';
      const conf = obj.confidence;
      const mode = obj.mode || 'heuristic';
      div.innerHTML = `
        <div class="fw-semibold">${title}</div>
        <div>
          <span class="fw-semibold sentiment-${band}">${fmt2(idx)}</span>
          <span class="text-body-secondary ms-2">置信度 ${fmt2(conf)} · ${mode}</span>
        </div>
      `;
      box.appendChild(div);
      const r = Array.isArray(obj.rationale) ? obj.rationale : [];
      if (r.length) {
        const ul = document.createElement('div');
        ul.className = 'text-body-secondary';
        ul.textContent = `理由：${r.slice(0, 3).join('；')}`;
        box.appendChild(ul);
      }
    }

    const w = agents.weights || {};
    const wLine = document.createElement('div');
    wLine.className = 'text-body-secondary';
    wLine.textContent = `权重：宏观 ${fmt2(w.macro)} · 品种 ${fmt2(w.symbol)} · 市场 ${fmt2(w.market)}`;
    box.appendChild(wLine);

    line('宏观 Agent', agents.macro);
    line('品种新闻 Agent', agents.symbol);
    if (agents.market && agents.market.status === 'ok') line('市场数据 Agent', agents.market);
    if (agents.final && agents.final.status === 'ok') line('最终情绪', agents.final);
  }

  function renderPlan(day) {
    const box = document.getElementById('planBox');
    const empty = document.getElementById('planEmpty');
    if (!box || !empty) return;
    box.innerHTML = '';

    const plan = day.plans;
    if (!plan || plan.status !== 'ok') {
      empty.classList.remove('d-none');
      return;
    }
    empty.classList.add('d-none');

    const head = document.createElement('div');
    head.className = 'text-body-secondary';
    head.textContent = `计划基于最近交易日 ${plan.asof || ''}`;
    box.appendChild(head);

    function renderBlock(title, obj) {
      const d = document.createElement('div');
      d.className = 'border rounded p-2';
      d.innerHTML = `
        <div class="fw-semibold mb-1">${title}</div>
        <div class="text-body-secondary">方向：${obj.direction || '--'} · 仓位：${obj.position || '--'}</div>
        <div class="text-body-secondary">入场区：${(obj.entry_zone || []).join(' ~ ')}</div>
        <div class="text-body-secondary">止损：${obj.stop ?? '--'} · 目标1：${obj.target1 ?? '--'} · 目标2：${obj.target2 ?? '--'}</div>
      `;
      box.appendChild(d);
    }

    renderBlock('短线', plan.short_term || {});
    renderBlock('波段', plan.swing || {});
    renderBlock('中线', plan.mid_term || {});
  }

  function renderExtras(day) {
    const box = document.getElementById('extrasBox');
    const empty = document.getElementById('extrasEmpty');
    if (!box || !empty) return;

    box.innerHTML = '';
    const modules = (day.extras && day.extras.modules) ? day.extras.modules : null;
    if (!modules || Object.keys(modules).length === 0) {
      empty.classList.remove('d-none');
      return;
    }
    empty.classList.add('d-none');

    const entries = Object.entries(modules);
    for (const [key, mod] of entries) {
      const status = (mod && mod.status) ? String(mod.status) : 'unknown';
      const hint = (mod && mod.hint) ? String(mod.hint) : key;
      const line = document.createElement('div');
      line.className = 'text-body-secondary';
      line.textContent = `- ${hint}（${status}）`;
      box.appendChild(line);
      const summary = (mod && mod.summary) ? String(mod.summary) : '';
      const count = (mod && Array.isArray(mod.items)) ? mod.items.length : 0;
      const tail = summary ? ` · ${summary}` : (count ? ` · ${count} items` : '');
      if (tail) {
        const tailLine = document.createElement('div');
        tailLine.className = 'text-body-secondary';
        tailLine.textContent = tail;
        box.appendChild(tailLine);
      }
    }
  }

  function buildKlineChart(canvas, days) {
    if (!canvas || !window.Chart) return null;
    const data = days.map(d => ({
      x: d.date,
      o: d.open,
      h: d.high,
      l: d.low,
      c: d.close,
    }));
    return new Chart(canvas.getContext('2d'), {
      type: 'candlestick',
      data: {
        datasets: [{
          label: 'K线',
          data,
        }]
      },
      options: {
        parsing: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { maxRotation: 0, autoSkip: true } },
          y: { position: 'right' }
        }
      }
    });
  }

  function buildVolChart(canvas, days, showSentiment) {
    if (!canvas || !window.Chart) return null;
    const labels = days.map(d => d.date);
    const vol = days.map(d => d.volume);
    const sent = days.map(d => d.sentiment);
    return new Chart(canvas.getContext('2d'), {
      type: 'bar',
      data: {
        labels,
        datasets: [
          {
            type: 'bar',
            label: '成交量',
            data: vol,
            yAxisID: 'y',
          },
          {
            type: 'line',
            label: '情绪指数',
            data: sent,
            yAxisID: 'y1',
            tension: 0.25,
            pointRadius: 1,
            hidden: !showSentiment,
          }
        ]
      },
      options: {
        plugins: { legend: { display: true } },
        scales: {
          y: { position: 'left', beginAtZero: true },
          y1: { position: 'right', min: -1, max: 1, grid: { drawOnChartArea: false } }
        }
      }
    });
  }

  async function initDetailPage() {
    if (!window.__SYMBOL__) return;
    const sym = window.__SYMBOL__;

    const picker = document.getElementById('datePicker');
    const csvLink = document.getElementById('csvLink');
    const corr20El = document.getElementById('corr20');
    const toggle = document.getElementById('toggleSentiment');
    csvLink.href = `${basePath}/api/exports/${sym.id}.csv`;

    const meta = await fetchJson(`${basePath}/api/symbols/${sym.id}/index.json`);
    if (corr20El) corr20El.textContent = String(meta.corr20);

    const days = (meta.days || []).slice(-30);
    const klineCanvas = document.getElementById('klineChart');
    const volCanvas = document.getElementById('volChart');
    let klineChart = buildKlineChart(klineCanvas, days);
    let volChart = buildVolChart(volCanvas, days, true);

    if (toggle) {
      toggle.addEventListener('change', () => {
        if (volChart) {
          volChart.data.datasets[1].hidden = !toggle.checked;
          volChart.update();
        }
      });
    }

    const latestDate = meta.latest_date || ((meta.days && meta.days.length) ? meta.days[meta.days.length - 1].date : null);
    if (picker && latestDate) picker.value = latestDate;

    async function loadDay(date) {
      try {
        const day = await fetchJson(`${basePath}/api/symbols/${sym.id}/days/${date}.json`);
        renderSummary(day);
        renderAgents(day);
        renderPlan(day);
        renderNews(day);
        renderExtras(day);
      } catch (e) {
        const el = document.getElementById('summaryCard');
        if (el) el.innerHTML = `<div class="text-body-secondary">数据更新中</div>`;
        const agentsEmpty = document.getElementById('agentsEmpty');
        if (agentsEmpty) agentsEmpty.classList.remove('d-none');
        const planEmpty = document.getElementById('planEmpty');
        if (planEmpty) planEmpty.classList.remove('d-none');
        const empty = document.getElementById('newsEmpty');
        if (empty) empty.classList.remove('d-none');
        const extrasEmpty = document.getElementById('extrasEmpty');
        if (extrasEmpty) extrasEmpty.classList.remove('d-none');
      }
    }

    if (latestDate) await loadDay(latestDate);
    if (picker) {
      picker.addEventListener('change', () => loadDay(picker.value));
    }
  }

  document.addEventListener('DOMContentLoaded', () => {
    initThemeToggle();
    initDetailPage().catch(() => {});
  });
})();
