// Dashboard del radar: sesión con Supabase Auth y buscador sobre el histórico.
//
// Toda la lógica de consulta vive en funciones de Postgres (buscar_procesos,
// resumen_radar). Esta capa sólo arma parámetros y pinta resultados, así que
// cambiar el criterio de búsqueda no obliga a tocar el frontend.
(() => {
  const $ = (id) => document.getElementById(id);

  // Fallar en voz alta. Si la librería no cargó o falta configuración, sin esta
  // guarda el script muere en la primera línea: el formulario se sigue viendo,
  // el botón deja de responder y no aparece ningún error en pantalla. Es el
  // modo de falla más confuso posible — parece que la página "no hace nada".
  function fatal(mensaje, detalle) {
    const caja = $('login-err');
    if (caja) caja.innerHTML = mensaje +
      (detalle ? `<br><span style="color:var(--muted)">${detalle}</span>` : '');
    const boton = document.querySelector('#form-login button');
    if (boton) { boton.disabled = true; boton.style.opacity = '.5'; }
    console.error('[radar]', mensaje, detalle || '');
  }

  const cfg = window.RADAR_CONFIG || {};

  if (typeof supabase === 'undefined' || !supabase.createClient) {
    fatal('No se pudo cargar la librería de Supabase.',
          'Revisá tu conexión o si alguna extensión del navegador bloquea cdn.jsdelivr.net.');
    return;
  }
  if (!cfg.SUPABASE_URL || !cfg.SUPABASE_ANON_KEY) {
    fatal('Falta configuración.', 'Revisá que config.js tenga SUPABASE_URL y SUPABASE_ANON_KEY.');
    return;
  }
  if (location.protocol === 'file:') {
    fatal('Abrí el dashboard desde un servidor, no con doble clic.',
          'En la carpeta web/: python3 -m http.server 8000 → http://localhost:8000');
    return;
  }

  const db = supabase.createClient(cfg.SUPABASE_URL, cfg.SUPABASE_ANON_KEY);
  const PAGINA = 50;
  let offset = 0;

  const money = (n) => {
    const v = Number(n);
    if (!v) return '—';
    return '$' + v.toLocaleString('es-CO', { maximumFractionDigits: 0 });
  };
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const fecha = (s) => (s ? String(s).slice(0, 10) : '—');

  // ---- Sesión ------------------------------------------------------------
  async function iniciar() {
    const { data: { session } } = await db.auth.getSession();
    if (session) mostrarApp(session);
  }

  function mostrarApp(session) {
    $('login').style.display = 'none';
    $('app').style.display = 'block';
    $('quien').textContent = session.user.email;
    cargarPerfiles();
    cargarResumen();
    buscar();
  }

  $('form-login').addEventListener('submit', async (e) => {
    e.preventDefault();
    $('login-err').textContent = '';
    let data, error;
    try {
      ({ data, error } = await db.auth.signInWithPassword({
        email: $('email').value.trim(),
        password: $('password').value,
      }));
    } catch (exc) {
      $('login-err').textContent = 'No se pudo contactar a Supabase: ' + exc.message;
      return;
    }
    if (error) {
      $('login-err').textContent = error.message === 'Invalid login credentials'
        ? 'Correo o contraseña incorrectos.' : error.message;
      return;
    }
    mostrarApp(data.session);
  });

  $('salir').addEventListener('click', async () => {
    await db.auth.signOut();
    location.reload();
  });

  // ---- Carga de datos ----------------------------------------------------
  async function cargarPerfiles() {
    const { data, error } = await db.rpc('perfiles_disponibles');
    if (error || !data) return;
    const sel = $('perfil');
    sel.innerHTML = '<option value="">Todos los perfiles</option>' +
      data.map((p) => `<option value="${esc(p.perfil)}">${esc(p.perfil)} (${p.n})</option>`).join('');
  }

  async function cargarResumen() {
    const { data, error } = await db.rpc('resumen_radar');
    if (error || !data) return;

    const total = data.reduce((a, r) => ({
      activos: a.activos + Number(r.activos || 0),
      alta: a.alta + Number(r.alta_prioridad || 0),
      pronto: a.pronto + Number(r.cierran_pronto || 0),
      retirados: a.retirados + Number(r.retirados || 0),
      valor: a.valor + Number(r.valor_total || 0),
    }), { activos: 0, alta: 0, pronto: 0, retirados: 0, valor: 0 });

    $('kpis').innerHTML = [
      [total.activos, 'En seguimiento'],
      [total.alta, 'Alta prioridad'],
      [total.pronto, 'Cierran en 10 días'],
      [total.retirados, 'Retirados del portal'],
      [money(total.valor), 'Valor en juego'],
    ].map(([v, l]) => `<div class="kpi"><div class="v">${v}</div><div class="l">${l}</div></div>`).join('');
  }

  async function buscar() {
    $('resultados').innerHTML = '<div class="estado">Buscando…</div>';

    const { data, error } = await db.rpc('buscar_procesos', {
      q: $('q').value.trim() || null,
      p_perfil: $('perfil').value || null,
      p_min_score: $('score').value ? Number($('score').value) : null,
      p_min_valor: $('valor').value ? Number($('valor').value) : null,
      p_solo_abiertos: $('abiertos').checked,
      p_incluir_retirados: $('retirados').checked,
      p_limit: PAGINA,
      p_offset: offset,
    });

    if (error) {
      $('resultados').innerHTML = `<div class="estado">Error: ${esc(error.message)}</div>`;
      return;
    }
    pintar(data || []);
  }

  function pintar(filas) {
    if (!filas.length) {
      $('resultados').innerHTML = offset === 0
        ? '<div class="estado">Sin resultados. Probá con menos filtros.</div>'
        : '<div class="estado">No hay más resultados.</div>';
      $('paginacion').innerHTML = offset > 0
        ? '<button class="ghost" id="prev">← Anteriores</button>' : '';
      engancharPaginacion(filas.length);
      return;
    }

    const filasHtml = filas.map((r) => {
      const s = Number(r.score) || 0;
      const cls = s >= 80 ? 'hot' : s >= 60 ? 'warm' : 'cool';
      const retirado = r.disappeared_at
        ? '<span class="meta" style="color:var(--hot)">Retirado del portal</span>' : '';
      const link = r.url
        ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">Ver ↗</a>`
        : '<span class="muted">—</span>';
      return `<tr>
        <td><span class="score ${cls}">${s}</span></td>
        <td class="nowrap muted">${esc(r.perfil)}</td>
        <td>
          <div class="objeto">${esc(r.objeto).slice(0, 260)}</div>
          <div class="meta">${esc(r.entidad)}${r.ciudad ? ' · ' + esc(r.ciudad) : ''}</div>
          <div class="reasons">${esc(r.reasons)}</div>
          ${retirado}
        </td>
        <td class="num">${money(r.valor)}</td>
        <td class="nowrap">${fecha(r.fecha_cierre)}</td>
        <td class="nowrap muted">${fecha(r.first_seen)}</td>
        <td class="nowrap">${link}</td>
      </tr>`;
    }).join('');

    $('resultados').innerHTML = `<table>
      <thead><tr>
        <th>Score</th><th>Perfil</th><th>Objeto / Entidad</th>
        <th class="num">Valor</th><th>Cierra</th><th>Detectado</th><th></th>
      </tr></thead>
      <tbody>${filasHtml}</tbody></table>`;

    $('paginacion').innerHTML = `
      ${offset > 0 ? '<button class="ghost" id="prev">← Anteriores</button>' : ''}
      <span>${offset + 1}–${offset + filas.length}</span>
      ${filas.length === PAGINA ? '<button class="ghost" id="next">Siguientes →</button>' : ''}`;
    engancharPaginacion(filas.length);
  }

  function engancharPaginacion(n) {
    const prev = $('prev'), next = $('next');
    if (prev) prev.onclick = () => { offset = Math.max(0, offset - PAGINA); buscar(); };
    if (next) next.onclick = () => { offset += PAGINA; buscar(); };
  }

  // ---- Interacción -------------------------------------------------------
  // Cualquier cambio de filtro vuelve a la primera página: mantener el offset
  // mostraría la página 3 de un resultado que quizá sólo tiene una.
  const reiniciar = () => { offset = 0; buscar(); };
  $('buscar').addEventListener('click', reiniciar);
  $('q').addEventListener('keydown', (e) => { if (e.key === 'Enter') reiniciar(); });
  ['perfil', 'score', 'abiertos', 'retirados'].forEach((id) =>
    $(id).addEventListener('change', reiniciar));

  iniciar();
})();
