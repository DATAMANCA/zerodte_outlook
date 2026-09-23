"""Renders the morning email (plain text + HTML) from today's scorecards."""
from datetime import date


def _fmt(x, spec: str = ".2f", suffix: str = "") -> str:
    if x is None:
        return "n/a"
    try:
        return f"{x:{spec}}{suffix}"
    except (ValueError, TypeError):
        return str(x)


def _build_subject(today: date, scorecards: dict) -> str:
    parts = [f"{t} {c['day']['label']}" for t, c in scorecards.items()]
    return f"0DTE Outlook {today.isoformat()}: " + " / ".join(parts)


def _factor_lines(factors: dict) -> list[str]:
    lines = []
    tech = factors.get("technical", {})
    if "gap_pct" in tech:
        lines.append(f"  Technical: gap {_fmt(tech.get('gap_pct'), '+.2f', '%')}, "
                      f"RSI14 {_fmt(tech.get('rsi14'), '.0f')}, "
                      f"vol20 {_fmt(tech.get('realized_vol_pct'), '.2f', '%')}")
    elif "pct_diff" in tech:
        lines.append(f"  Technical: 5d/20d SMA gap {_fmt(tech.get('pct_diff'), '+.2f', '%')}")

    iv = factors.get("iv_regime", {})
    lines.append(f"  IV regime: VIX {_fmt(iv.get('vix'), '.1f')} "
                 f"(20d avg {_fmt(iv.get('vix_20d_avg'), '.1f')}), "
                 f"VIX9D/VIX {_fmt(iv.get('term_ratio'), '.2f')}")

    flow = factors.get("options_flow", {})
    if flow:
        cal = "calibrated" if flow.get("calibrated") else "not yet calibrated (need more logged days)"
        lines.append(f"  Options flow: put/call OI {_fmt(flow.get('put_call_oi_ratio'), '.2f')} "
                     f"(z {_fmt(flow.get('put_call_oi_zscore'), '+.2f')}, {cal}), "
                     f"spot vs zero-gamma flip {_fmt(flow.get('spot_vs_flip_pct'), '+.2f', '%')}, "
                     f"max pain {_fmt(flow.get('max_pain'), '.1f')}")

    macro = factors.get("macro", {})
    events = macro.get("events")
    if events:
        lines.append(f"  Macro: today - {', '.join(events)}")
    lines.append(f"  Macro: {macro.get('week_event_count', 0)} high-impact event day(s) this week "
                 f"(confidence dampener only, no directional signal)")

    sent = factors.get("sentiment", {})
    if sent.get("available"):
        lines.append(f"  Sentiment: Fear & Greed {_fmt(sent.get('fear_greed'), '.0f')}")
    else:
        lines.append("  Sentiment: unavailable this run")

    return lines


def _ticker_section_text(ticker: str, card: dict, accuracy: dict) -> list[str]:
    lines = [f"=== {ticker} (spot {_fmt(card['chain_snapshot']['spot'], '.2f')}) ==="]
    for horizon, label in (("day", "DAY"), ("week", "WEEK")):
        h = card[horizon]
        acc = accuracy.get((ticker, horizon))
        acc_str = f" | live track record: {acc['hit_rate']:.0%} over {acc['n']} calls" if acc and acc["n"] >= 5 else " | live track record: still accumulating"
        lines.append(f"{label}: {h['label']} (composite {h['composite']:+.2f}, confidence {h['confidence']:.0%}){acc_str}")
        lines.extend(_factor_lines(h["factors"]))
        lines.append("")
    return lines


def render_text(today: date, scorecards: dict, rates_dollar: dict, today_events: list[str],
                 week_count: int, fear_greed, accuracy: dict) -> str:
    lines = [f"0DTE SPY/QQQ Outlook - {today.isoformat()}", ""]
    if today_events:
        lines.append(f"** High-impact macro event(s) today: {', '.join(today_events)} - expect lower "
                     f"confidence / choppier action **")
    lines.append(f"This week: {week_count} high-impact macro event day(s) scheduled.")
    lines.append(f"10Y yield: {_fmt(rates_dollar.get('tnx'), '.2f')}% "
                 f"({_fmt(rates_dollar.get('tnx_chg'), '+.2f')}) | "
                 f"Dollar index ({rates_dollar.get('dxy_symbol') or 'n/a'}): "
                 f"{_fmt(rates_dollar.get('dxy'), '.2f')} ({_fmt(rates_dollar.get('dxy_chg'), '+.2f')})")
    lines.append("")

    for ticker, card in scorecards.items():
        lines.extend(_ticker_section_text(ticker, card, accuracy))

    lines.append(
        "DISCLAIMER: Paper/analysis tool only, not investment advice. Free data "
        "sources only (yfinance, FRED, unofficial endpoints) - not a substitute "
        "for a real-time dealer-positioning feed. The options-flow/GEX reading is "
        "an approximation computed from today's chain and cannot be backtested; "
        "only its live track record (above, once enough days have accumulated) "
        "indicates how reliable it's actually been. 'Week' = 5 trading days "
        "forward, not strictly the calendar Friday."
    )
    return "\n".join(lines)


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def render_html(today: date, scorecards: dict, rates_dollar: dict, today_events: list[str],
                 week_count: int, fear_greed, accuracy: dict) -> str:
    text = render_text(today, scorecards, rates_dollar, today_events, week_count, fear_greed, accuracy)
    label_colors = {"Bullish": "#0a7d2c", "Bearish": "#b00020", "Neutral": "#666"}
    html = ["<html><body style='font-family:Arial,sans-serif;font-size:14px;color:#222'>"]
    html.append(f"<h2>0DTE SPY/QQQ Outlook - {today.isoformat()}</h2>")
    if today_events:
        html.append(f"<p style='color:#b00020'><b>High-impact macro event(s) today: "
                    f"{_html_escape(', '.join(today_events))}</b> - expect lower confidence / choppier action</p>")
    html.append(f"<p>This week: {week_count} high-impact macro event day(s) scheduled.<br>"
               f"10Y yield: {_fmt(rates_dollar.get('tnx'), '.2f')}% "
               f"({_fmt(rates_dollar.get('tnx_chg'), '+.2f')}) &middot; "
               f"Dollar index: {_fmt(rates_dollar.get('dxy'), '.2f')} "
               f"({_fmt(rates_dollar.get('dxy_chg'), '+.2f')})</p>")

    for ticker, card in scorecards.items():
        html.append(f"<h3>{ticker} <span style='color:#666;font-weight:normal'>"
                    f"(spot {_fmt(card['chain_snapshot']['spot'], '.2f')})</span></h3>")
        for horizon, label in (("day", "DAY"), ("week", "WEEK")):
            h = card[horizon]
            color = label_colors.get(h["label"], "#222")
            acc = accuracy.get((ticker, horizon))
            acc_str = f" &middot; live track record: {acc['hit_rate']:.0%} over {acc['n']} calls" if acc and acc["n"] >= 5 else " &middot; live track record: still accumulating"
            html.append(f"<p><b>{label}:</b> <span style='color:{color};font-weight:bold'>{h['label']}</span> "
                       f"(composite {h['composite']:+.2f}, confidence {h['confidence']:.0%}){acc_str}</p>")
            html.append("<ul style='margin-top:0'>")
            for line in _factor_lines(h["factors"]):
                html.append(f"<li>{_html_escape(line.strip())}</li>")
            html.append("</ul>")

    html.append(
        "<p style='color:#666;font-size:12px'>Paper/analysis tool only, not investment advice. "
        "Free data sources only (yfinance, FRED, unofficial endpoints) - not a substitute for a "
        "real-time dealer-positioning feed. The options-flow/GEX reading is an approximation "
        "computed from today's chain and cannot be backtested; only its live track record above "
        "(once enough days have accumulated) indicates how reliable it's actually been. "
        "'Week' = 5 trading days forward, not strictly the calendar Friday.</p>"
    )
    html.append("</body></html>")
    return "\n".join(html)


def render(today: date, scorecards: dict, rates_dollar: dict, today_events: list[str],
           week_count: int, fear_greed, accuracy: dict) -> tuple[str, str, str]:
    subject = _build_subject(today, scorecards)
    text = render_text(today, scorecards, rates_dollar, today_events, week_count, fear_greed, accuracy)
    html = render_html(today, scorecards, rates_dollar, today_events, week_count, fear_greed, accuracy)
    return subject, text, html
