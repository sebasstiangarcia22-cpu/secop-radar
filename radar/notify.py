"""Email alerts.

Plain SMTP so it works with a Gmail app password and no third-party account.
Credentials come from the environment (GitHub Actions secrets in production).
"""

import logging
import os
import smtplib
from email.message import EmailMessage

log = logging.getLogger(__name__)


def _money(value):
    try:
        return f"${float(value):,.0f} COP"
    except (TypeError, ValueError):
        return "Valor no publicado"


def build_email_html(new_matches: list, vanished: list, stats: dict,
                     schema: dict, perfil: str = "") -> str:
    from .fields import get

    rows = []
    for _identifier, match in new_matches:
        record = match.record
        objeto = get(record, schema, "objeto") or "(sin descripcion)"
        entidad = get(record, schema, "entidad") or "(entidad no identificada)"
        valor = _money(get(record, schema, "valor"))
        url = get(record, schema, "url") or ""
        colour = "#d13438" if match.score >= 80 else "#f0a30a" if match.score >= 60 else "#5a5a5a"

        link = f'<a href="{url}" style="color:#0b6bcb;">Ver en SECOP</a>' if url else ""
        rows.append(f"""
        <tr>
          <td style="padding:14px 12px;border-bottom:1px solid #e6e6e6;vertical-align:top;">
            <div style="display:inline-block;background:{colour};color:#fff;font-weight:700;
                        border-radius:4px;padding:3px 9px;font-size:13px;">{match.score}%</div>
          </td>
          <td style="padding:14px 12px;border-bottom:1px solid #e6e6e6;">
            <div style="font-weight:600;font-size:15px;color:#111;">{objeto[:220]}</div>
            <div style="color:#555;font-size:13px;margin-top:5px;">{entidad}</div>
            <div style="color:#111;font-size:13px;margin-top:5px;font-weight:600;">{valor}</div>
            <div style="color:#777;font-size:12px;margin-top:5px;">{" | ".join(match.reasons)}</div>
            <div style="margin-top:7px;font-size:13px;">{link}</div>
          </td>
        </tr>""")

    vanished_block = ""
    if vanished:
        items = "".join(
            f"<li style='margin-bottom:7px;'><b>{v.get('objeto') or v['id']}</b><br>"
            f"<span style='color:#666;font-size:12px;'>Visible desde {v['first_seen']} "
            f"hasta {v['last_seen']}</span></li>"
            for v in vanished[:15]
        )
        vanished_block = f"""
        <h3 style="margin:28px 0 10px;color:#d13438;font-size:16px;">
          Procesos retirados del portal ({len(vanished)})
        </h3>
        <p style="color:#555;font-size:13px;margin:0 0 10px;">
          Estos procesos coincidian con los criterios y ya no aparecen en SECOP.
          Quedan archivados aqui con su ventana real de publicacion.
        </p>
        <ul style="padding-left:18px;font-size:14px;color:#111;">{items}</ul>"""

    table = f"""
      <table style="width:100%;border-collapse:collapse;">{"".join(rows)}</table>
    """ if rows else "<p style='color:#555;'>Sin coincidencias nuevas en este barrido.</p>"

    return f"""<!doctype html>
<html><body style="margin:0;padding:24px;background:#f5f6f8;
      font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Arial,sans-serif;">
  <div style="max-width:720px;margin:0 auto;background:#fff;border-radius:8px;
              padding:28px;border:1px solid #e2e4e8;">
    <h2 style="margin:0 0 4px;font-size:20px;color:#111;">
      Radar SECOP{f" — {perfil}" if perfil else ""}
    </h2>
    <p style="margin:0 0 22px;color:#666;font-size:13px;">
      {len(new_matches)} oportunidad(es) nueva(s) &middot;
      {stats.get('activos', 0)} en seguimiento &middot;
      {stats.get('desaparecidos', 0)} retiradas historicamente
    </p>
    {table}
    {vanished_block}
    <p style="margin-top:28px;color:#999;font-size:11px;border-top:1px solid #eee;padding-top:14px;">
      Generado automaticamente a partir de datos abiertos de SECOP
      (Colombia Compra Eficiente) via datos.gov.co.
    </p>
  </div>
</body></html>"""


def send_email(subject: str, html: str, to: list | None = None) -> bool:
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASS")
    recipients = to or [r.strip() for r in os.environ.get("ALERT_TO", "").split(",") if r.strip()]

    if not (user and password and recipients):
        log.error("Faltan credenciales SMTP o destinatarios; no se envia correo.")
        return False

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = os.environ.get("SMTP_FROM", user)
    message["To"] = ", ".join(recipients)
    message.set_content("Este correo requiere un cliente compatible con HTML.")
    message.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(user, password)
            server.send_message(message)
        log.info("Correo enviado a %s", recipients)
        return True
    except Exception as exc:  # noqa: BLE001 - never let a mail failure kill the sweep
        log.error("Fallo el envio de correo: %s", exc)
        return False
