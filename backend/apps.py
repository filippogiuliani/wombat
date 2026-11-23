"""The apps connected to Wombat, from the environment:

  WOMBAT_APPS=kooka,wallaby,pelican
  WOMBAT_APP_KOOKA_URL=https://kooka.example.com      address people open (links on the home page)
  WOMBAT_APP_KOOKA_INTERNAL_URL=http://kooka:3001      optional: how Wombat reaches it (import), default the URL
  WOMBAT_APP_KOOKA_KEY=…                                 shared secret, the same as KOOKA_HUB_KEY in that app
  WOMBAT_APP_KOOKA_NAME / _DESCRIPTION                   optional: shown on the home page

Quokka (the webmail) is connected the same way (WOMBAT_APP_QUOKKA_URL / _KEY, QUOKKA_HUB_KEY there) but only asks:
who signed in with Pocket ID, and Kooka/Pelican on their behalf (see service.ALLOWED).

`python manage.py new-key` makes a key. The key lets the app ask Wombat who a person is and what their family looks
like; Wombat uses it to read the app's family data during the import."""
import hmac
import os

DEFAULTS = {
    'kooka': ('Kooka', 'Cose da fare, scadenze, appuntamenti, spesa', 'To-dos, deadlines, appointments, shopping'),
    'wallaby': ('Wallaby', 'Svezzamento: pasti, dispensa, menu', 'Weaning: meals, pantry, menu'),
    'pelican': ('Pelican', 'Archivio dei documenti di famiglia', 'Family document archive'),
    'quokka': ('Quokka', 'Posta di famiglia', 'Family webmail'),
}
# Apps that only ask (Quokka: who is this person, and the other apps through Wombat): they keep no family of their
# own, so Wombat neither imports from them nor tells them about changes nor asks them for the home summary.
ASK_ONLY = {'quokka'}


def _env(app_id, key, default=''):
    return os.environ.get(f'WOMBAT_APP_{app_id.upper()}_{key}', default).strip()


def ids():
    return [a.strip().lower() for a in os.environ.get('WOMBAT_APPS', '').split(',') if a.strip()]


def get(app_id):
    if app_id not in ids():
        return None
    name, desc_it, desc_en = DEFAULTS.get(app_id, (app_id, '', ''))
    url = _env(app_id, 'URL').rstrip('/')
    return {
        'id': app_id, 'name': _env(app_id, 'NAME', name), 'url': url,
        'internal_url': (_env(app_id, 'INTERNAL_URL') or url).rstrip('/'),
        'description': {'it': _env(app_id, 'DESCRIPTION', desc_it), 'en': _env(app_id, 'DESCRIPTION', desc_en)},
        'key': _env(app_id, 'KEY'),
        'serves': app_id not in ASK_ONLY,
    }


def public():
    """Apps as the interface sees them (no keys)."""
    out = []
    for i in ids():
        a = get(i)
        out.append({k: a[k] for k in ('id', 'name', 'url', 'description')})
    return out


def by_key(presented):
    """The app whose key was presented (constant-time comparison), or None."""
    if not presented:
        return None
    for i in ids():
        key = _env(i, 'KEY')
        if key and len(key) >= 24 and hmac.compare_digest(key, presented):
            return get(i)
    return None
