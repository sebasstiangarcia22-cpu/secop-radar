// Conexión a Supabase para el dashboard.
//
// La publishable key es SEGURA acá: está diseñada para el navegador y sola no
// da acceso a nada — las políticas de Row Level Security exigen sesión iniciada
// para devolver una sola fila. La service_role key NUNCA va en este archivo:
// vive como secret de GitHub Actions y salta RLS por diseño.
window.RADAR_CONFIG = {
  SUPABASE_URL: 'https://pnjskododsukelzwvyzz.supabase.co',
  SUPABASE_ANON_KEY: 'sb_publishable_hhfyan3A1e5TdRIokjaqlw_Z02CrDj8',
};
