(function () {
  function resolveBasePath(raw) {
    const s = String(raw || '').trim();
    if (!s) return '';
    try {
      if (s.startsWith('/')) return s.replace(/\/$/, '');
      if (s.startsWith('http://') || s.startsWith('https://')) {
        return new URL(s).pathname.replace(/\/$/, '');
      }
      const u = new URL(s.replace(/\/?$/, '/'), window.location.href);
      return u.pathname.replace(/\/$/, '');
    } catch (e) {
      return '';
    }
  }

  function tryRegisterFinancial() {
    const Chart = window.Chart;
    if (!Chart || typeof Chart.register !== 'function') return;

    const candidates = [
      window.ChartFinancial,
      window.chartjsChartFinancial,
      window.chartjs_chart_financial,
      window['chartjs-chart-financial'],
    ].filter(Boolean);

    for (const mod of candidates) {
      try {
        const CandlestickController = mod.CandlestickController;
        const CandlestickElement = mod.CandlestickElement;
        const OhlcController = mod.OhlcController;
        const OhlcElement = mod.OhlcElement;
        const FinancialController = mod.FinancialController;
        const FinancialElement = mod.FinancialElement;

        const parts = [
          CandlestickController,
          CandlestickElement,
          OhlcController,
          OhlcElement,
          FinancialController,
          FinancialElement,
        ].filter(Boolean);

        if (parts.length) {
          Chart.register(...parts);
          return;
        }
      } catch (e) {
        // ignore
      }
    }
  }

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

  const explicitBasePath = resolveBasePath(window.__BASE_PATH__ || '');
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

    // Use agents.final.index (multi-agent weighted score) when available;
    // fall back to the simple lexicon-based news sentiment.index.
    const agents = day.agents || {};
    const final = agents.final || {};
    let s, band;
    if (final.status === 'ok' && typeof final.index === 'number') {
      s = final.index;
      band = final.band || 'neutral';
    } else {
      s = day.sentiment.index;
      band = day.sentiment.band;
    }
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

    function fmtAmount(v) {
      if (typeof v !== 'number' || !isFinite(v)) return '--';
      const a = Math.abs(v);
      if (a >= 1e8) return `${(v / 1e8).toFixed(2)}亿`;
      if (a >= 1e4) return `${(v / 1e4).toFixed(2)}万`;
      return String(Math.round(v));
    }

    const amountText = priceUnavailable ? '--' : fmtAmount(price.amount);
    const turnoverText = priceUnavailable ? '--' : (typeof price.turnover_rate === 'number' ? `${fmt2(price.turnover_rate)}%` : '--');
    const extraLine = (!priceUnavailable && (price.amount != null || price.turnover_rate != null))
      ? ` · 成交额：${amountText} · 换手率：${turnoverText}`
      : '';
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
          <div class="text-body-secondary">涨跌幅：${pctText} · 成交量：${volText}${extraLine}</div>
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

  function buildLineChart(canvas, labels, data, label, yMin, yMax) {
    if (!canvas || !window.Chart) return null;
    try {
      return new window.Chart(canvas.getContext('2d'), {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label,
            data,
            tension: 0.25,
            pointRadius: 1,
          }]
        },
        options: {
          plugins: { legend: { display: true } },
          scales: {
            y: {
              position: 'right',
              min: (typeof yMin === 'number') ? yMin : undefined,
              max: (typeof yMax === 'number') ? yMax : undefined,
            }
          }
        }
      });
    } catch (e) {
      return null;
    }
  }

  function buildBarChart(canvas, labels, data, label) {
    if (!canvas || !window.Chart) return null;
    try {
      return new window.Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
          labels,
          datasets: [{
            label,
            data,
          }]
        },
        options: {
          plugins: { legend: { display: true } },
          scales: {
            y: { position: 'right' }
          }
        }
      });
    } catch (e) {
      return null;
    }
  }

  function formatNum(v, digits) {
    if (v === null || v === undefined) return '--';
    const n = Number(v);
    if (!Number.isFinite(n)) return String(v);
    const d = (typeof digits === 'number') ? digits : 2;
    return n.toFixed(d);
  }

  function pickColumns(rows) {
    if (!rows || !rows.length) return [];
    const preferred = [
      'rank', '排名',
      'member', 'member_name', '会员简称', '会员',
      'long', '多单', '多头', '多单持仓',
      'short', '空单', '空头', '空单持仓',
      'net', '净持仓',
      'volume', 'vol', '成交量', '成交',
      'change', '增减',
    ];
    const keys = new Set();
    for (const r of rows) {
      if (r && typeof r === 'object') {
        for (const k of Object.keys(r)) keys.add(k);
      }
    }
    const cols = [];
    for (const k of preferred) {
      if (keys.has(k) && !cols.includes(k)) cols.push(k);
      if (cols.length >= 8) break;
    }
    if (cols.length < 3) {
      for (const k of Array.from(keys)) {
        if (!cols.includes(k)) cols.push(k);
        if (cols.length >= 8) break;
      }
    }
    return cols;
  }

  function renderTable(rows, maxRows) {
    const data = Array.isArray(rows) ? rows.slice(0, maxRows || 10) : [];
    if (!data.length) return null;
    const cols = pickColumns(data);
    if (!cols.length) return null;

    const wrap = document.createElement('div');
    wrap.className = 'table-responsive';

    const table = document.createElement('table');
    table.className = 'table table-sm table-striped mb-0 text-nowrap';
    const thead = document.createElement('thead');
    const hr = document.createElement('tr');
    for (const c of cols) {
      const th = document.createElement('th');
      th.textContent = String(c);
      hr.appendChild(th);
    }
    thead.appendChild(hr);
    table.appendChild(thead);

    const tbody = document.createElement('tbody');
    for (const r of data) {
      const tr = document.createElement('tr');
      for (const c of cols) {
        const td = document.createElement('td');
        const v = (r && typeof r === 'object') ? r[c] : '';
        if (typeof v === 'number') td.textContent = formatNum(v, 2);
        else td.textContent = (v === null || v === undefined) ? '' : String(v);
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);
    wrap.appendChild(table);
    return wrap;
  }

  function renderFundamentals(fund) {
    const box = document.getElementById('fundBox');
    const empty = document.getElementById('fundEmpty');
    const updatedAt = document.getElementById('fundUpdatedAt');
    const summary = document.getElementById('fundSummary');
    if (!box || !empty) return;
    box.innerHTML = '';

    if (!fund || !fund.symbol) {
      empty.classList.remove('d-none');
      if (summary) summary.textContent = '';
      return;
    }
    empty.classList.add('d-none');
    if (updatedAt) updatedAt.textContent = fund.updated_at ? `更新：${fund.updated_at}` : '';

    function oneLine(title, obj) {
      const status = obj && obj.status ? String(obj.status) : 'unknown';
      const hint = obj && obj.hint ? String(obj.hint) : '';
      const text = obj && obj.summary ? String(obj.summary) : '';
      return `${title}：${status}${hint ? `（${hint}）` : ''}${text ? ` · ${text}` : ''}`;
    }

    const lines = [];
    if (fund.inventory) lines.push(oneLine('库存', fund.inventory));
    if (fund.spot_basis) lines.push(oneLine('基差', fund.spot_basis));
    if (fund.roll_yield) lines.push(oneLine('展期', fund.roll_yield));
    if (fund.positions_rank) lines.push(oneLine('持仓', fund.positions_rank));
    if (summary) summary.textContent = lines.join(' | ');

    // Latest snapshot (human readable)
    const snap = document.createElement('div');
    snap.className = 'text-body-secondary small';
    const invLast = (fund.inventory && Array.isArray(fund.inventory.series)) ? fund.inventory.series[fund.inventory.series.length - 1] : null;
    const basisLast = (fund.spot_basis && Array.isArray(fund.spot_basis.series)) ? fund.spot_basis.series[fund.spot_basis.series.length - 1] : null;
    const ryLast = (fund.roll_yield && Array.isArray(fund.roll_yield.series)) ? fund.roll_yield.series[fund.roll_yield.series.length - 1] : null;
    const posLast = (fund.positions_rank && Array.isArray(fund.positions_rank.series)) ? fund.positions_rank.series[fund.positions_rank.series.length - 1] : null;
    const snapParts = [];
    if (invLast) snapParts.push(`库存: ${formatNum(invLast.inventory, 2)} / 变动: ${formatNum(invLast.change, 2)}`);
    if (basisLast) snapParts.push(`主力基差: ${formatNum(basisLast.dom_basis, 2)} / 基差率: ${formatNum(basisLast.dom_basis_rate, 4)}`);
    if (ryLast) snapParts.push(`展期收益率: ${formatNum(ryLast.roll_yield, 4)}`);
    if (posLast) snapParts.push(`净持仓: ${formatNum(posLast.net, 2)} (多: ${formatNum(posLast.long, 2)} / 空: ${formatNum(posLast.short, 2)})`);
    snap.textContent = snapParts.length ? snapParts.join(' | ') : '';
    if (snap.textContent) box.appendChild(snap);

    function addStatusNote(title, obj, series) {
      const status = obj && obj.status ? String(obj.status) : 'unknown';
      const hint = obj && obj.hint ? String(obj.hint) : '';
      const n = Array.isArray(series) ? series.length : 0;
      let msg = '';
      if (status !== 'ok') {
        msg = hint ? hint : `${title}（${status}）`;
      } else if (n > 0 && n < 2) {
        msg = `${title}：已收集 ${n} 个点，至少 2 个点后绘图`;
      } else if (n === 0) {
        msg = `${title}：暂无数据`;
      }
      if (!msg) return;
      const line = document.createElement('div');
      line.className = 'text-body-secondary small';
      line.textContent = msg;
      box.appendChild(line);
    }

    function addChartBlock(title, canvasId, buildFn) {
      const wrap = document.createElement('div');
      wrap.className = 'border rounded p-2';
      wrap.innerHTML = `<div class="fw-semibold mb-2">${title}</div><canvas id="${canvasId}" height="160"></canvas>`;
      box.appendChild(wrap);
      const canvas = wrap.querySelector('canvas');
      buildFn(canvas);
    }

    const invSeries = (fund.inventory && Array.isArray(fund.inventory.series)) ? fund.inventory.series : [];
    addStatusNote('库存', fund.inventory, invSeries);
    if (invSeries.length >= 2) {
      const s = invSeries.slice(-60);
      addChartBlock('库存（近 60 点）', 'invChart', (canvas) => {
        buildLineChart(canvas, s.map(x => x.date), s.map(x => x.inventory), '库存', null, null);
      });
    }

    const basisSeries = (fund.spot_basis && Array.isArray(fund.spot_basis.series)) ? fund.spot_basis.series : [];
    addStatusNote('基差', fund.spot_basis, basisSeries);
    if (basisSeries.length >= 2) {
      const s = basisSeries.slice(-60);
      addChartBlock('主力基差（近 60 点）', 'basisChart', (canvas) => {
        buildLineChart(canvas, s.map(x => x.date), s.map(x => x.dom_basis), '主力基差', null, null);
      });
    }

    const rySeries = (fund.roll_yield && Array.isArray(fund.roll_yield.series)) ? fund.roll_yield.series : [];
    addStatusNote('展期', fund.roll_yield, rySeries);
    if (rySeries.length >= 2) {
      const s = rySeries.slice(-60);
      addChartBlock('展期收益率（近 60 点）', 'ryChart', (canvas) => {
        buildLineChart(canvas, s.map(x => x.date), s.map(x => x.roll_yield), '展期收益率', null, null);
      });
    }

    const posSeries = (fund.positions_rank && Array.isArray(fund.positions_rank.series)) ? fund.positions_rank.series : [];
    addStatusNote('持仓', fund.positions_rank, posSeries);
    if (posSeries.length >= 2) {
      const s = posSeries.slice(-60);
      addChartBlock('净持仓（近 60 点）', 'posChart', (canvas) => {
        buildBarChart(canvas, s.map(x => x.date), s.map(x => x.net), '净持仓');
      });
    }

    const preview = (fund.positions_rank && Array.isArray(fund.positions_rank.latest_preview)) ? fund.positions_rank.latest_preview : [];
    if (preview.length) {
      const wrap = document.createElement('div');
      wrap.className = 'border rounded p-2';
      wrap.innerHTML = `<div class="fw-semibold mb-2">持仓排名（预览）</div>`;
      const table = renderTable(preview, 10);
      if (table) {
        wrap.appendChild(table);
      } else {
        const t = document.createElement('div');
        t.className = 'text-body-secondary small';
        t.textContent = '预览数据格式不稳定，暂无法表格化展示';
        wrap.appendChild(t);
      }
      box.appendChild(wrap);
    }
  }

  function buildKlineChart(canvas, days) {
    if (!canvas || !window.Chart) return null;
    tryRegisterFinancial();

    const Chart = window.Chart;
    const labels = days.map(d => d.date);
    const ctx = canvas.getContext('2d');
    if (!ctx) return null;

    // Use a category x scale to avoid requiring an external date adapter
    // (timeseries/time scale will throw if no adapter is present).
    const candleData = days.map((d, i) => ({
      x: i,
      o: d.open,
      h: d.high,
      l: d.low,
      c: d.close,
    }));

    try {
      return new Chart(ctx, {
        type: 'candlestick',
        data: {
          labels,
          datasets: [{
            label: 'K线',
            data: candleData,
          }]
        },
        options: {
          parsing: false,
          plugins: { legend: { display: false } },
          scales: {
            x: { type: 'category', ticks: { maxRotation: 0, autoSkip: true } },
            y: { position: 'right' }
          }
        }
      });
    } catch (e) {
      // Fallback: render a close-price line so the page doesn't stay blank.
      try {
        return new Chart(ctx, {
          type: 'line',
          data: {
            labels,
            datasets: [{
              label: '收盘价',
              data: days.map(d => d.close),
              tension: 0.25,
              pointRadius: 0,
              borderWidth: 2,
            }]
          },
          options: {
            plugins: { legend: { display: false } },
            scales: {
              x: { ticks: { maxRotation: 0, autoSkip: true } },
              y: { position: 'right' }
            }
          }
        });
      } catch (_e2) {
        return null;
      }
    }
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

    // fundamentals
    try {
      const fund = await fetchJson(`${basePath}/api/symbols/${sym.id}/fundamentals.json`);
      renderFundamentals(fund);
    } catch (e) {
      const empty = document.getElementById('fundEmpty');
      if (empty) empty.classList.remove('d-none');
    }

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
    tryRegisterFinancial();
    initThemeToggle();
    initDetailPage().catch((e) => {
      try {
        // Keep the page usable even if some module fails.
        console.error(e);
        const el = document.getElementById('summaryCard');
        if (el) {
          const msg = (e && e.message) ? String(e.message) : String(e || 'unknown');
          el.innerHTML = `<div class="text-danger">页面脚本错误：${msg}</div>`;
        }
      } catch (_ignore) {
        // ignore
      }
    });
  });
})();
