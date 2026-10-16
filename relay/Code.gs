/**
 * SpinMate mail relay: a Google Apps Script web app that sends the SpinMate welcome email (logo, greeting,
 * user guide and presentation as PDFs) from the Google account that deploys it.
 *
 * The app sends only {name, email, app_url}. The email text, the logo and the PDFs are read from the public
 * repository, so they always match the latest version on main. Setup: relay/README.md.
 */
const RAW = 'https://raw.githubusercontent.com/Michaellianeris/sublattice-agent/main/web/';
const DAILY_LIMIT = 80;            // consumer Gmail sends to about 100 recipients a day
const EMAIL_RE = /^[^@\s<>"']{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$/;
const ATTACHED = 'The user guide and the SpinMate presentation are attached as PDFs';

function doGet() {
  return json_({ok: true, service: 'spinmate-mail-relay', remaining: MailApp.getRemainingDailyQuota()});
}

function doPost(e) {
  try {
    return json_(handle_(JSON.parse((e.postData && e.postData.contents) || '{}')));
  } catch (err) {
    return json_({ok: false, error: String((err && err.message) || err)});
  }
}

function handle_(req) {
  const email = String(req.email || '').trim();
  const name = String(req.name || '').replace(/[\r\n<>]/g, '').trim().slice(0, 40) || 'there';
  let appUrl = String(req.app_url || '');
  if (!/^https?:\/\/[^\s"'<>]{1,200}$/.test(appUrl)) appUrl = 'http://localhost:8421';
  if (!EMAIL_RE.test(email)) return {ok: false, error: 'That does not look like an email address.'};

  const lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    const props = PropertiesService.getScriptProperties();
    const day = Utilities.formatDate(new Date(), 'UTC', 'yyyyMMdd');
    clean_(props, day);
    const toKey = 'to:' + day + ':' + hash_(email.toLowerCase());
    if (props.getProperty(toKey)) return {ok: false, error: 'A welcome email was already sent to this address today.'};
    const count = Number(props.getProperty('count:' + day) || 0);
    if (count >= DAILY_LIMIT || MailApp.getRemainingDailyQuota() < 1) {
      return {ok: false, error: 'The SpinMate mail relay reached its daily limit; try again tomorrow.'};
    }
    send_(name, email, appUrl);
    props.setProperty('count:' + day, String(count + 1));
    props.setProperty(toKey, '1');
    return {ok: true};
  } finally {
    lock.releaseLock();
  }
}

function send_(name, email, appUrl) {
  const get = file => UrlFetchApp.fetch(RAW + file).getBlob();
  let html = get('welcome-email.html').getDataAsString('UTF-8');
  if (html.indexOf('<!--') === 0) html = html.slice(html.indexOf('-->') + 3).trim();   // the note at the top
  html = html.split('{{name}}').join(esc_(name)).split('{{app_url}}').join(esc_(appUrl)).split('{{attached}}').join(ATTACHED);
  MailApp.sendEmail({
    to: email,
    name: 'SpinMate',
    subject: 'Welcome to SpinMate, ' + name,
    body: 'Welcome to SpinMate, ' + name + '!\n\nYour account is ready. ' + ATTACHED + '.\nOpen the app: ' + appUrl + '\n',
    htmlBody: html,
    inlineImages: {'spinmate-logo': get('spinmate-lockup.png').setContentType('image/png')},
    attachments: [
      get('spinmate-guide.pdf').setContentType('application/pdf').setName('SpinMate-User-Guide.pdf'),
      get('spinmate-presentation.pdf').setContentType('application/pdf').setName('SpinMate-Presentation.pdf'),
    ],
  });
}

// Run once from the editor: asks for the permissions and sends the email to your own address.
function testSend() {
  send_('Michael', Session.getEffectiveUser().getEmail(), 'http://localhost:8421');
}

function clean_(props, day) {                    // forget the counters of earlier days
  props.getKeys().forEach(k => { if (k.indexOf(':' + day) < 0) props.deleteProperty(k); });
}

function hash_(s) {
  return Utilities.base64EncodeWebSafe(Utilities.computeDigest(Utilities.DigestAlgorithm.SHA_256, s)).slice(0, 22);
}

function esc_(s) {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

function json_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
