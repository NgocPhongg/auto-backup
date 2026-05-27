import json as _json
async def test():
        async def click_dropdown(kind):
            _kind_js = _json.dumps(kind)
            pos = await cdp.evaluate("""
                (() => {
                    const kind = """ + _kind_js + """;
                    const visible = (el) => {
                        const r = el.getBoundingClientRect();
                        const st = window.getComputedStyle(el);
                        return r.width > 0 && r.height > 0 &&
                            r.right > 0 && r.bottom > 0 && r.left < window.innerWidth && r.top < window.innerHeight &&
                            st.display !== 'none' && st.visibility !== 'hidden';
                    };
                    const center = (el) => {
                        const r = el.getBoundingClientRect();
                        return {x: r.left + r.width / 2, y: r.top + r.height / 2};
                    };
                    // Strip icon chars and whitespace from text
                    const cleanText = (el) => {
                        const t = ((el.innerText || el.value || el.textContent || el.getAttribute('aria-label') || '') + '').trim();
                        // Remove non-ASCII symbols/icons, keep digits, colon, dash, slash, letters
                        return t.replace(/[^\x00-\x7E\u00C0-\u024F]/g, ' ').replace(/\s+/g, ' ').trim();
                    };
                    const hasTime = (t) => /\b\d{1,2}:\d{2}\b/.test(t);
                    const hasDate = (t) => /\b\d{4}-\d{2}-\d{2}\b/.test(t) ||
                                           /\b\d{1,2}\/\d{1,2}\/\d{4}\b/.test(t) ||
                                           /\b(thg|thang|jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\b/i.test(t);

                    // Search broadly - TikTok dropdowns can be wider than 260px
                    const controls = Array.from(document.querySelectorAll(
                        'button, input, [role="button"], [role="combobox"], [aria-haspopup], [aria-expanded], div, span'
                    ))
                        .filter(visible)
                        .map(el => {
                            const r = el.getBoundingClientRect();
                            const t = cleanText(el);
                            return {el, r, t};
                        })
                        .filter(x => x.r.width >= 30 && x.r.width <= 450 && x.r.height >= 20 && x.r.height <= 80);

                    let matches = controls.filter(x => kind === 'time' ? hasTime(x.t) : hasDate(x.t));

                    // Fallback by aria-label
                    if (!matches.length) {
                        const sel = kind === 'time'
                            ? '[aria-label*="time" i], [aria-label*="gio" i], [class*="TimePicker"], [class*="timePicker"]'
                            : '[aria-label*="date" i], [aria-label*="ngay" i], [data-e2e*="date"], [class*="DatePicker"], [class*="datepicker"]';
                        try {
                            const byAttr = Array.from(document.querySelectorAll(sel))
                                .filter(visible)
                                .map(el => { const r = el.getBoundingClientRect(); return {el, r, t: cleanText(el)}; })
                                .filter(x => x.r.width >= 30 && x.r.width <= 450 && x.r.height >= 20 && x.r.height <= 80);
                            if (byAttr.length) matches = byAttr;
                        } catch(e) {}
                    }

                    // Positional fallback: in schedule area, time=left, date=right
                    if (!matches.length) {
                        const drps = controls.filter(x => {
                            const st = window.getComputedStyle(x.el);
                            return st.cursor === 'pointer' || x.el.getAttribute('aria-haspopup') ||
                                   x.el.getAttribute('role') === 'combobox' || x.el.getAttribute('role') === 'button';
                        }).sort((a, b) => b.r.top - a.r.top);
                        if (drps.length >= 2) {
                            const bot2 = drps.slice(0, 2).sort((a, b) => a.r.left - b.r.left);
                            matches = [kind === 'time' ? bot2[0] : bot2[1]].filter(Boolean);
                        }
                    }

                    if (!matches.length) return null;
                    matches.sort((a, b) => a.r.top - b.r.top || (kind === 'time' ? a.r.left - b.r.left : b.r.left - a.r.left));
                    return center(matches[0].el);
                })()
""")
            return await click_center(pos)
