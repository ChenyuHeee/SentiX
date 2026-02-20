(function () {
  const basePath = (window.__BASE_PATH__ || '').replace(/\/$/, '');

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
    el.innerHTML = `
      <div class="d-flex flex-wrap justify-content-between gap-3">
        <div>
          <div class="text-body-secondary">今日情绪指数</div>
          <div class="fs-2 fw-semibold sentiment-${band}">${fmt2(s)} <span class="fs-5 fw-normal">(${sentimentText(s)})</span></div>
          <div class="text-body-secondary">更新：${day.updated_at}</div>
        </div>
        <div>
          <div class="text-body-secondary">收盘价</div>
          <div class="fs-4 fw-semibold">${fmt2(day.price.close)}</div>
          <div class="text-body-secondary">涨跌幅：${fmt2(day.price.pct_change)}% · 成交量：${day.price.volume}</div>
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
      const aStart = it.url ? `<a href="${it.url}" target="_blank" rel="noopener noreferrer">` : '<span>';
      const aEnd = it.url ? '</a>' : '</span>';
      const div = document.createElement('div');
      div.className = 'news-item';
      div.innerHTML = `
        <div class="fw-semibold">${emo} ${aStart}${it.title}${aEnd}</div>
        <div class="news-meta">来源：${it.source || '--'} · ${it.published_at || ''} · 置信度 ${fmt2(it.confidence)}</div>
      `;
      list.appendChild(div);
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

    const latestDate = (meta.days && meta.days.length) ? meta.days[meta.days.length - 1].date : null;
    if (picker && latestDate) picker.value = latestDate;

    async function loadDay(date) {
      try {
        const day = await fetchJson(`${basePath}/api/symbols/${sym.id}/days/${date}.json`);
        renderSummary(day);
        renderNews(day);
      } catch (e) {
        const el = document.getElementById('summaryCard');
        if (el) el.innerHTML = `<div class="text-body-secondary">数据更新中</div>`;
        const empty = document.getElementById('newsEmpty');
        if (empty) empty.classList.remove('d-none');
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
