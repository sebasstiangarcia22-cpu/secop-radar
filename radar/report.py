"""Generador del dashboard estático.

Escribe un único HTML autocontenido desde el archivo local, para que se pueda
servir por GitHub Pages, abrir desde el disco o adjuntar a un correo sin
ningún backend detrás.
"""

import html
import json
from datetime import datetime, timezone
from pathlib import Path

# Paleta estable por posición: cada perfil conserva su color entre corridas.
COLORES_PERFIL = ["#0b6bcb", "#8b5cf6", "#0f9d58", "#e08600", "#d13438", "#0891b2"]


def _money(value):
    try:
        return f"${float(value):,.0f}"
    except (TypeError, ValueError):
        return "—"


def _escape(value):
    return html.escape(str(value or ""))


def _slug(value):
    return "".join(c if c.isalnum() else "-" for c in str(value or "").lower())


def build_dashboard(matches: list, vanished: list, stats: dict,
                    perfiles: list, stats_por_perfil: dict | None = None) -> str:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    stats_por_perfil = stats_por_perfil or {}

    nombres = [p["nombre"] for p in perfiles]
    color_de = {n: COLORES_PERFIL[i % len(COLORES_PERFIL)] for i, n in enumerate(nombres)}

    def badge(perfil):
        color = color_de.get(perfil, "#6b7280")
        return (f'<span class="perfil" style="background:{color}1a;color:{color};'
                f'border:1px solid {color}55;">{_escape(perfil)}</span>')

    def row(item):
        score = item.get("score") or 0
        cls = "hot" if score >= 80 else "warm" if score >= 60 else "cool"
        url = item.get("url") or ""
        link = (f'<a href="{_escape(url)}" target="_blank" rel="noopener">Ver ↗</a>'
                if url else "<span class='muted'>—</span>")
        return f"""
        <tr data-perfil="{_slug(item.get('perfil'))}">
          <td><span class="score {cls}">{score}</span></td>
          <td>{badge(item.get('perfil'))}</td>
          <td>
            <div class="objeto">{_escape(item.get('objeto'))[:260]}</div>
            <div class="meta">{_escape(item.get('entidad'))}</div>
            <div class="reasons">{_escape(item.get('reasons'))}</div>
          </td>
          <td class="num">{_money(item.get('valor'))}</td>
          <td class="nowrap">{_escape((item.get('fecha_cierre') or '')[:10]) or '—'}</td>
          <td class="nowrap muted">{_escape(item.get('first_seen', '')[:16])}</td>
          <td class="nowrap">{link}</td>
        </tr>"""

    rows = "".join(row(m) for m in matches) or (
        '<tr><td colspan="7" class="empty">Sin oportunidades activas todavía.</td></tr>')

    vanished_rows = "".join(f"""
        <tr>
          <td>{badge(v.get('perfil'))}</td>
          <td>{_escape(v.get('objeto'))[:220]}</td>
          <td class="nowrap muted">{_escape(v.get('first_seen', '')[:16])}</td>
          <td class="nowrap muted">{_escape(v.get('last_seen', '')[:16])}</td>
          <td class="nowrap alert">{_escape(v.get('disappeared_at', '')[:16])}</td>
        </tr>""" for v in vanished) or (
        '<tr><td colspan="5" class="empty">Ningún proceso retirado hasta ahora.</td></tr>')

    tarjetas = "".join(f"""
      <div class="kpi" style="border-left:3px solid {color_de.get(n, '#6b7280')};">
        <div class="v">{stats_por_perfil.get(n, {}).get('activos', 0) or 0}</div>
        <div class="l">{_escape(n)}</div>
      </div>""" for n in nombres)

    filtros = "".join(
        f'<button class="chip" data-filtro="{_slug(n)}">{_escape(n)}</button>'
        for n in nombres)

    cobertura = ", ".join(sorted({
        d for p in perfiles for d in p.get("geografia", {}).get("departamentos", [])
    })) or "Nacional"

    return f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Radar SECOP</title>
<style>
  :root {{
    --bg:#f6f7f9; --card:#fff; --ink:#14171a; --muted:#6b7280;
    --line:#e5e7eb; --accent:#0b6bcb; --hot:#d13438; --warm:#e08600; --cool:#6b7280;
  }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg:#0e1116; --card:#161b22; --ink:#e6edf3; --muted:#8b949e;
             --line:#30363d; --accent:#58a6ff; }}
  }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; padding:28px 20px; background:var(--bg); color:var(--ink);
         font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }}
  .wrap {{ max-width:1240px; margin:0 auto; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .sub {{ color:var(--muted); font-size:13px; margin:0 0 22px; }}
  .kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));
           gap:12px; margin-bottom:20px; }}
  .kpi {{ background:var(--card); border:1px solid var(--line); border-radius:8px; padding:16px; }}
  .kpi .v {{ font-size:26px; font-weight:700; }}
  .kpi .l {{ color:var(--muted); font-size:12px; text-transform:uppercase;
             letter-spacing:.5px; margin-top:3px; }}
  .filtros {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:18px; }}
  .chip {{ background:var(--card); border:1px solid var(--line); color:var(--ink);
           border-radius:20px; padding:6px 14px; font-size:13px; cursor:pointer;
           font-family:inherit; }}
  .chip:hover {{ border-color:var(--accent); }}
  .chip.on {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
  .panel {{ background:var(--card); border:1px solid var(--line);
            border-radius:8px; padding:20px; margin-bottom:24px; overflow-x:auto; }}
  h2 {{ font-size:15px; margin:0 0 14px; }}
  table {{ width:100%; border-collapse:collapse; font-size:13px; min-width:860px; }}
  th {{ text-align:left; color:var(--muted); font-size:11px; text-transform:uppercase;
        letter-spacing:.5px; padding:0 10px 10px; border-bottom:1px solid var(--line); }}
  td {{ padding:12px 10px; border-bottom:1px solid var(--line); vertical-align:top; }}
  .objeto {{ font-weight:600; line-height:1.4; }}
  .meta {{ color:var(--muted); font-size:12px; margin-top:4px; }}
  .reasons {{ color:var(--muted); font-size:11px; margin-top:5px; font-style:italic; }}
  .score {{ display:inline-block; min-width:38px; text-align:center; color:#fff;
            font-weight:700; border-radius:4px; padding:3px 7px; font-size:12px; }}
  .score.hot {{ background:var(--hot); }} .score.warm {{ background:var(--warm); }}
  .score.cool {{ background:var(--cool); }}
  .perfil {{ display:inline-block; border-radius:12px; padding:2px 9px;
             font-size:11px; font-weight:600; white-space:nowrap; }}
  .num {{ text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }}
  .nowrap {{ white-space:nowrap; }} .muted {{ color:var(--muted); }}
  .alert {{ color:var(--hot); font-weight:600; }}
  .empty {{ text-align:center; color:var(--muted); padding:28px; }}
  a {{ color:var(--accent); text-decoration:none; }} a:hover {{ text-decoration:underline; }}
  footer {{ color:var(--muted); font-size:11px; text-align:center; margin-top:30px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Radar SECOP</h1>
  <p class="sub">Cobertura: {_escape(cobertura)} &middot; Actualizado {generated}</p>

  <div class="kpis">
    <div class="kpi"><div class="v">{stats.get('activos', 0)}</div><div class="l">En seguimiento</div></div>
    <div class="kpi"><div class="v">{len([m for m in matches if (m.get('score') or 0) >= 80])}</div><div class="l">Alta prioridad</div></div>
    <div class="kpi"><div class="v">{stats.get('desaparecidos', 0)}</div><div class="l">Retirados</div></div>
    {tarjetas}
  </div>

  <div class="filtros">
    <button class="chip on" data-filtro="todos">Todos</button>
    {filtros}
  </div>

  <div class="panel">
    <h2>Oportunidades activas</h2>
    <table>
      <thead><tr>
        <th>Score</th><th>Perfil</th><th>Objeto / Entidad</th><th class="num">Valor</th>
        <th>Cierra</th><th>Detectado</th><th></th>
      </tr></thead>
      <tbody id="filas">{rows}</tbody>
    </table>
  </div>

  <div class="panel">
    <h2>Procesos retirados del portal</h2>
    <table>
      <thead><tr>
        <th>Perfil</th><th>Objeto</th><th>Primera vez visto</th>
        <th>Última vez visto</th><th>Retirado</th>
      </tr></thead>
      <tbody>{vanished_rows}</tbody>
    </table>
  </div>

  <footer>Datos abiertos de SECOP — Colombia Compra Eficiente, vía datos.gov.co.</footer>
</div>
<script>
  document.querySelectorAll('.chip').forEach(function (chip) {{
    chip.addEventListener('click', function () {{
      document.querySelectorAll('.chip').forEach(function (c) {{ c.classList.remove('on'); }});
      chip.classList.add('on');
      var filtro = chip.dataset.filtro;
      document.querySelectorAll('#filas tr').forEach(function (fila) {{
        var visible = filtro === 'todos' || fila.dataset.perfil === filtro;
        fila.style.display = visible ? '' : 'none';
      }});
    }});
  }});
</script>
</body>
</html>"""


def write_dashboard(path, matches, vanished, stats, perfiles, stats_por_perfil=None):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        build_dashboard(matches, vanished, stats, perfiles, stats_por_perfil),
        encoding="utf-8")
    return path


def write_json_feed(path, matches):
    """Feed legible por máquina, útil para conectar otras herramientas."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = [{k: v for k, v in m.items() if k != "raw"} for m in matches]
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
