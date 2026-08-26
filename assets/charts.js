// Shared, DOM-free chart helpers used by index.html and embed.html.
// Pure functions only: palettes, data transforms, ECharts option builders,
// and the concordats/type-table computation. All page state (filters,
// event wiring, chips) stays in the pages themselves.
window.SLCharts = (function () {
    'use strict';

    // Muted categorical palette — validated (CVD-safe adjacent pairs on
    // white; low-contrast slots relieved by direct labels).
    const PALETTE = ['#4a86c9', '#4e9a4e', '#d987aa', '#d9a441',
                     '#3aa985', '#d97a4e', '#6a5cb8', '#d16a69'];
    const GRAY = '#9a9a94';
    const BLUE_RAMP = ['#c7dcf5', '#9ec4ec', '#74abe3', '#4a91da', '#2a78d6', '#1f5eae'];
    const INK = '#1a1a1a', INK2 = '#52514e';

    function enTypeFactory(typeLabels) {
        return t => (typeLabels[t] && typeLabels[t].en) || t;
    }
    function typeColorFactory(typeOrder) {
        return t => {
            const i = typeOrder.indexOf(t);
            return i >= 0 && i < PALETTE.length ? PALETTE[i] : GRAY;
        };
    }

    // Aggregate the stats.json year×canton×type cube with explicit filters.
    function aggregateCube(cube, { y1 = null, y2 = null, canton = null, type = null,
                                   byCanton = false, byType = false, byYear = false,
                                   useCanton = true, useType = true } = {}) {
        const out = {};
        for (const y of Object.keys(cube).sort()) {
            if ((y1 && y < y1) || (y2 && y > y2)) continue;
            for (const [c, types] of Object.entries(cube[y])) {
                if (useCanton && canton && c !== canton) continue;
                for (const [t, n] of Object.entries(types)) {
                    if (useType && type && t !== type) continue;
                    const key = byCanton ? c : byType ? t : byYear ? y : 'all';
                    if (byYear && byType) {
                        (out[y] ??= {})[t] = ((out[y] ??= {})[t] || 0) + n;
                    } else {
                        out[key] = (out[key] || 0) + n;
                    }
                }
            }
        }
        return out;
    }

    const nodeName = m => m.identifier + ' ' +
        (m.title.en || m.title.de || m.title.fr || '');

    // harmonized_categories.json tree → ECharts node list for one scope.
    function harmTreeToNodes(nodes, scope, topColor) {
        return nodes.map((n, i) => {
            const c = topColor || (i < PALETTE.length ? PALETTE[i] : GRAY);
            const node = {
                name: nodeName(n),
                value: n[scope],
                _meta: n,
                itemStyle: topColor ? undefined : { color: c },
            };
            if (n.children) {
                const kids = harmTreeToNodes(n.children, scope, c).filter(k => k.value > 0);
                if (kids.length) node.children = kids;
            }
            return node;
        }).filter(n => n.value > 0);
    }

    function domainTooltip(p) {
        const m = p.data && p.data._meta;
        if (!m) return p.name;
        const t = m.title || {};
        return `<b>${m.identifier}</b> ${t.en || t.de || ''}` +
            (t.de && t.en ? `<br><span style="color:#888">${t.de}</span>` : '') +
            (t.fr ? `<br><span style="color:#888">${t.fr}</span>` : '') +
            (t.it ? `<br><span style="color:#888">${t.it}</span>` : '') +
            `<br>Total: <b>${m.total.toLocaleString()}</b>` +
            ` · Federal: ${m.federal.toLocaleString()}` +
            ` · Cantonal: ${m.cantonal.toLocaleString()}`;
    }

    // ─── ECharts option builders ────────────────────────────────────────────
    function cantonTreemapOption(data) {
        return {
            tooltip: { formatter: p => `${p.name}: <b>${p.value.toLocaleString()}</b> laws` },
            series: [{
                type: 'treemap', roam: false, nodeClick: false,
                breadcrumb: { show: false },
                itemStyle: { borderColor: '#fff', borderWidth: 2, gapWidth: 2 },
                label: { color: '#fff', fontSize: 12,
                         formatter: p => `${p.name}\n${p.value.toLocaleString()}` },
                color: BLUE_RAMP, colorMappingBy: 'value',
                data,
            }],
        };
    }

    function typesDonutOption(data) {
        return {
            tooltip: { formatter: p =>
                `${p.name}: <b>${p.value.toLocaleString()}</b> (${p.percent}%)` },
            legend: { bottom: 0, type: 'scroll', textStyle: { color: INK2, fontSize: 11 } },
            series: [{
                type: 'pie', radius: ['40%', '66%'], center: ['50%', '44%'],
                itemStyle: { borderColor: '#fff', borderWidth: 2, borderRadius: 3 },
                label: { color: INK, fontSize: 11,
                         formatter: p => `${p.name}\n${p.value.toLocaleString()}` },
                labelLine: { length: 8, length2: 6 },
                data,
            }],
        };
    }

    function yearsStackOption(years, activeTypes, perYear, enType, typeColor) {
        return {
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { top: 0, type: 'scroll',
                      data: activeTypes.map(enType),
                      textStyle: { color: INK2, fontSize: 11 } },
            grid: { left: 50, right: 20, top: 30, bottom: 40 },
            xAxis: { type: 'category', data: years,
                     axisLabel: { color: INK2 } },
            yAxis: { type: 'value', axisLabel: { color: INK2 },
                     splitLine: { lineStyle: { color: '#eee' } } },
            series: activeTypes.map(t => ({
                name: enType(t), type: 'bar', stack: 'laws',
                itemStyle: { color: typeColor(t) },
                emphasis: { focus: 'series' },
                data: years.map(y => perYear[y][t] || 0),
            })),
        };
    }

    function domainsTreemapOption(data) {
        return {
            tooltip: { formatter: domainTooltip },
            series: [{
                type: 'treemap', data,
                leafDepth: 2, roam: false,
                breadcrumb: { show: true, top: 0 },
                itemStyle: { borderColor: '#fff', borderWidth: 2, gapWidth: 2 },
                upperLabel: { show: true, height: 22, color: '#fff' },
                label: { color: '#fff', fontSize: 11 },
                colorSaturation: [0.3, 0.55],
            }],
        };
    }

    function domainsSunburstOption(data) {
        return {
            tooltip: { formatter: domainTooltip },
            series: [{
                type: 'sunburst', data,
                radius: ['12%', '92%'],
                nodeClick: 'rootToNode',
                itemStyle: { borderColor: '#fff', borderWidth: 1.5 },
                label: { minAngle: 8, color: INK, fontSize: 10,
                         formatter: p => p.name.split(' ')[0] },
            }],
        };
    }

    // ─── Canton × domain table (concordats and per-type tables) ─────────────
    // conc: a concordats_by_domain.json-shaped object.
    function concTable(conc, y1, y2) {
        const keys = conc.domains.map(d => d.key);
        const filtered = !!(y1 || y2) && !!conc.by_year;
        let cantons = conc.cantons, totals = conc.totals;
        if (filtered) {
            cantons = {}; totals = Object.fromEntries(keys.map(k => [k, 0]));
            totals.total = 0;
            for (const c of Object.keys(conc.cantons))
                cantons[c] = Object.fromEntries([...keys.map(k => [k, 0]), ['total', 0]]);
            for (const [y, perCanton] of Object.entries(conc.by_year)) {
                if (y === 'unknown' || (y1 && y < y1) || (y2 && y > y2)) continue;
                for (const [c, row] of Object.entries(perCanton)) {
                    for (const [k, n] of Object.entries(row)) {
                        cantons[c][k] += n; cantons[c].total += n;
                        totals[k] += n; totals.total += n;
                    }
                }
            }
        }
        let undated = 0;
        for (const r of Object.values((conc.by_year || {}).unknown || {}))
            undated += Object.values(r).reduce((a, b) => a + b, 0);
        return { keys, filtered, cantons, totals, undated };
    }

    // `companion` (optional) is a second grand total on a DIFFERENT counting
    // unit — {label, value} — rendered as an extra footer row. The concordat
    // table counts signatory memberships while the year chart above it counts
    // published copies; showing one number without the other is what made the
    // two look contradictory.
    function concTableHTML(conc, { keys, cantons, totals }, highlightCanton, companion) {
        const cell = v => `<td${v === 0 ? ' class="zero"' : ''}>${v.toLocaleString()}</td>`;
        const head = '<tr><th>Canton</th>' +
            conc.domains.map(d => `<th>${d.label_en || d.label_fr}</th>`).join('') + '<th>Total</th></tr>';
        const body = Object.entries(cantons).map(([canton, row]) => {
            const hl = highlightCanton === canton
                ? ' style="background:#fdeceb;font-weight:600"' : '';
            return `<tr${hl}><td>${canton}</td>${keys.map(k => cell(row[k])).join('')}${cell(row.total)}</tr>`;
        }).join('');
        let foot = `<tr><td>${companion ? 'Total (memberships)' : 'Total'}</td>` +
            keys.map(k => `<td>${totals[k].toLocaleString()}</td>`).join('') +
            `<td>${totals.total.toLocaleString()}</td></tr>`;
        if (companion) {
            foot += `<tr class="companion-total"><td>${companion.label}</td>` +
                `<td colspan="${keys.length}" style="text-align:left;font-weight:400">` +
                `${companion.note || ''}</td>` +
                `<td>${companion.value.toLocaleString()}</td></tr>`;
        }
        return `<table class="data-table"><thead>${head}</thead><tbody>${body}</tbody><tfoot>${foot}</tfoot></table>`;
    }

    // The second unit for a concordat view: published copies, filtered on the
    // same years, from concordats_by_domain.json. Returns null when that file
    // isn't loaded or the view isn't a signatory view.
    function copiesCompanion(concCopies, y1, y2) {
        if (!concCopies) return null;
        return {
            label: 'Total (published copies)',
            note: 'the unit counted by the year chart above — one per canton publishing the act',
            value: concTable(concCopies, y1, y2).totals.total,
        };
    }

    // ─── chstat-style views for the canton × domain tables ─────────────────
    // "Graphique": stacked bar, cantons × domains.
    function concChartOption(conc, tbl) {
        const cantons = Object.keys(tbl.cantons);
        const doms = conc.domains.map((d, i) => ({
            key: d.key, name: d.label_en || d.label_fr,
            color: i < PALETTE.length ? PALETTE[i] : GRAY,
        }));
        return {
            tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' } },
            legend: { top: 0, type: 'scroll', textStyle: { color: INK2, fontSize: 11 } },
            grid: { left: 50, right: 20, top: 30, bottom: 40 },
            xAxis: { type: 'category', data: cantons,
                     axisLabel: { color: INK2, fontSize: 10 } },
            yAxis: { type: 'value', axisLabel: { color: INK2 },
                     splitLine: { lineStyle: { color: '#eee' } } },
            series: doms.map(d => ({
                name: d.name, type: 'bar', stack: 'm',
                itemStyle: { color: d.color },
                emphasis: { focus: 'series' },
                data: cantons.map(c => tbl.cantons[c][d.key] || 0),
            })),
        };
    }

    // "Carte": dependency-free canton tile map (approximate geographic grid).
    const CANTON_TILES = {
        BS: [3, 0], SH: [6, 0],
        JU: [2, 1], SO: [3, 1], BL: [4, 1], AG: [5, 1], ZH: [6, 1], TG: [7, 1],
        NE: [2, 2], BE: [3, 2], LU: [4, 2], ZG: [5, 2], SZ: [6, 2], SG: [7, 2], AR: [8, 2],
        VD: [1, 3], FR: [2, 3], OW: [4, 3], NW: [5, 3], UR: [6, 3], GL: [7, 3], AI: [8, 3],
        GE: [0, 4], VS: [2, 4], TI: [5, 4], GR: [7, 4],
    };
    function tileMapHTML(conc, tbl) {
        const max = Math.max(1, ...Object.values(tbl.cantons).map(r => r.total || 0));
        const shade = v => BLUE_RAMP[Math.min(BLUE_RAMP.length - 1,
            Math.floor((v / max) * BLUE_RAMP.length))];
        const tiles = Object.entries(CANTON_TILES).map(([c, [col, row]]) => {
            const r = tbl.cantons[c] || { total: 0 };
            const tip = conc.domains.map(d =>
                `${d.label_en || d.label_fr}: ${r[d.key] || 0}`).join('\n');
            const light = (r.total || 0) / max > 0.45;
            return `<div class="tile" style="grid-column:${col + 1};grid-row:${row + 1};` +
                `background:${shade(r.total || 0)};color:${light ? '#fff' : '#1a1a1a'}" ` +
                `title="${c} — total ${(r.total || 0).toLocaleString()}\n${tip}">` +
                `<b>${c}</b><span>${(r.total || 0).toLocaleString()}</span></div>`;
        }).join('');
        return `<div class="tile-map">${tiles}</div>`;
    }


    // ─── Legal event stream (publications + revisions) ──────────────────
    // A second counting unit for the same corpus: the trend charts above
    // count each law once, under its enactment year; these count every
    // (law, date) pair, so a law revised 47 times contributes 47 records.
    // Publications keep the categorical blue used for the primary series;
    // revisions take the muted red — the pair clears CVD separation and
    // both sit on the site palette.
    const EVENT_COLORS = { publication: PALETTE[0], revision: PALETTE[7] };

    // events_by_year.json cube → flat per-year series for one scope.
    // 'all' sums the scopes rather than reading a precomputed total, so the
    // scope buttons cannot disagree with the stacked bars they filter.
    function eventsSeries(cube, { scope = 'all', from = null, to = null } = {}) {
        const years = cube.years.filter(y => (!from || y >= from) && (!to || y <= to));
        const KEYS = ['publication', 'revision', 'lines', 'revisions_with_delta',
                      'articles_publication', 'articles_revision'];
        const pick = y => {
            const cell = cube.by_year[y] || {};
            const scopes = scope === 'all' ? Object.keys(cell) : [scope];
            return scopes.reduce((a, s) => {
                const c = cell[s] || {};
                KEYS.forEach(k => { a[k] += c[k] || 0; });
                return a;
            }, Object.fromEntries(KEYS.map(k => [k, 0])));
        };
        const rows = years.map(pick);
        const col = k => rows.map(r => r[k]);
        return {
            years,
            publications: col('publication'),
            revisions: col('revision'),
            lines: col('lines'),
            withDelta: col('revisions_with_delta'),
            articlesPublished: col('articles_publication'),
            articlesRevised: col('articles_revision'),
        };
    }

    // Three measures over the same bars, and each is its own quantity — the
    // y-scale is swapped outright when the measure changes, never shared.
    //   count    events            both scopes
    //   articles articles touched  both scopes  (from the markdown bodies)
    //   lines    lines changed     federal      (needs two versions to diff)
    const EVENT_MEASURES = {
        count: {
            axis: 'events', total: 'Total events',
            parts: [['publications', 'Publications (first appearance)', 'Publications'],
                    ['revisions', 'Revisions', 'Revisions']],
        },
        articles: {
            axis: 'articles', total: 'Total articles',
            parts: [['articlesPublished', 'Articles published (first appearance)',
                     'Articles published'],
                    ['articlesRevised', 'Articles revised', 'Articles revised']],
        },
        lines: {
            axis: 'lines changed',
            parts: [['lines', 'Lines changed (added + removed)', 'Lines changed']],
        },
    };

    // `width` is the container's pixel width, and the option is genuinely
    // different at 342px than at 1052px: a phone cannot carry 37 year-bands, a
    // legend row and an axis name in the same top-left corner.
    function eventsStackOption(data, measure, width) {
        const spec = EVENT_MEASURES[measure] || EVENT_MEASURES.count;
        const stacked = spec.parts.length > 1;
        const W = width || 900;
        const narrow = W < 560;
        const gridL = narrow ? 44 : 62;
        const gridR = narrow ? 12 : 20;
        const band = Math.max(1, (W - gridL - gridR) / Math.max(1, data.years.length));
        // Bars are capped at 24px and never fill their band — the leftover is air.
        const barW = Math.max(1, Math.min(24, band * 0.7));
        // The 2px surface gap between stacked segments is drawn as a stroke, so
        // it costs 4px of bar width as well as the 2px it buys vertically.
        // Below ~12px that erases the mark it was meant to separate — the phone
        // rendered a comb of hairlines — so a narrow band drops the stroke and
        // leans on the blue/red pair, which is far enough apart to carry it.
        const sep = stacked && barW >= 12 ? 2 : 0;
        const colors = [EVENT_COLORS.publication, EVENT_COLORS.revision];

        const series = spec.parts.map(([key, longName, shortName], i) => {
            const last = i === spec.parts.length - 1;
            return {
                type: 'bar', barMaxWidth: 24, barCategoryGap: '30%',
                emphasis: { focus: 'series' },
                name: narrow ? shortName : longName,
                stack: stacked ? 'events' : undefined,
                itemStyle: {
                    color: stacked ? colors[i] : EVENT_COLORS.revision,
                    borderColor: '#fff', borderWidth: sep,
                    borderRadius: last ? [3, 3, 0, 0] : 0,
                },
                data: data[key],
            };
        });

        return {
            tooltip: {
                trigger: 'axis', axisPointer: { type: 'shadow' },
                // a phone tooltip that escapes the canvas is unreadable
                confine: true,
                formatter: params => {
                    if (!params.length) return '';
                    const y = params[0].axisValue;
                    const i = data.years.indexOf(y);
                    const rows = params.map(p =>
                        `${p.marker}${p.seriesName}: <b>${(p.value || 0).toLocaleString()}</b>`);
                    if (spec.total) {
                        const tot = spec.parts.reduce((a, [k]) => a + (data[k][i] || 0), 0);
                        rows.push(`<span style="color:${INK2}">${spec.total}: ` +
                                  `<b>${tot.toLocaleString()}</b></span>`);
                    } else if (data.withDelta[i]) {
                        rows.push(`<span style="color:${INK2}">over ` +
                                  `${data.withDelta[i].toLocaleString()} measured revisions</span>`);
                    }
                    return `<b>${y}</b><br>${rows.join('<br>')}`;
                },
            },
            // On a phone the legend sits below the plot: at the top it lands on
            // the y-axis name, which is what produced the "ePubls" overprint.
            legend: narrow
                ? { bottom: 0, itemGap: 10, itemWidth: 12, itemHeight: 8,
                    textStyle: { color: INK2, fontSize: 10 } }
                : { top: 0, textStyle: { color: INK2, fontSize: 11 } },
            grid: { left: gridL, right: gridR,
                    top: narrow ? 24 : 30, bottom: narrow ? 56 : 40 },
            xAxis: { type: 'category', data: data.years,
                     axisLabel: { color: INK2, fontSize: narrow ? 10 : 12 } },
            yAxis: {
                type: 'value',
                name: spec.axis,
                nameTextStyle: { color: INK2, fontSize: 11, align: 'left' },
                nameGap: 12,
                axisLabel: { color: INK2, fontSize: narrow ? 10 : 12,
                             formatter: v => v >= 1000 ? (v / 1000) + 'k' : v },
                splitLine: { lineStyle: { color: '#eee' } },
            },
            series,
        };
    }

    // indicators.json → the workload figures for one year and scope.
    // 'all' is read from its own cell, never assembled here: a pooled median
    // is not the average of the per-scope medians, and only the generator has
    // the underlying gaps.
    function eventIndicators(ind, year, scope) {
        const cell = ((ind.by_year || {})[year] || {})[scope];
        if (!cell) return null;
        return {
            year,
            events: cell.volume || 0,
            revisions: cell.revisions || 0,
            lawsInForce: cell.laws_in_force || 0,
            churn: cell.churn,
            instability: cell.instability,
            medianGapMonths: cell.median_gap_months,
            linesChanged: cell.lines_changed || 0,
            articlesRevised: cell.articles_revised || 0,
            articleChurn: cell.article_churn,
        };
    }

    return {
        PALETTE, GRAY, BLUE_RAMP, INK, INK2,
        enTypeFactory, typeColorFactory, aggregateCube,
        nodeName, harmTreeToNodes, domainTooltip,
        cantonTreemapOption, typesDonutOption, yearsStackOption,
        domainsTreemapOption, domainsSunburstOption,
        concTable, concTableHTML, copiesCompanion, concChartOption, tileMapHTML,
        EVENT_COLORS, eventsSeries, eventsStackOption, eventIndicators,
    };
})();
