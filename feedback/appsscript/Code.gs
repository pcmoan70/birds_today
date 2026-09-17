/**
 * Bird Calendar — feedback sink (Google Apps Script web app).
 *
 * Receives one vote per POST from docs/feedback.js and writes it as a small
 * JSON file into a Drive folder you own. scripts/feedback_refresh.py reads that
 * folder and replaces downvoted images.
 *
 * Deploy:
 *   1. script.google.com → New project → paste this file.
 *   2. Deploy → New deployment → type "Web app".
 *        Execute as:      Me
 *        Who has access:  Anyone
 *      (Anyone = anyone may POST a vote; the script still runs as you, so only
 *      it can touch your Drive. Nobody can read the folder through this URL:
 *      doGet returns a status line and nothing else.)
 *   3. Copy the /exec URL into ENDPOINT in docs/feedback.js.
 *   4. Open the /exec URL once in a browser — it reports the folder it will
 *      write to, creating it if needed.
 *
 * The folder is found by name in My Drive, so it survives redeployment. Set
 * FOLDER_ID instead if you want a specific existing folder (e.g. one already
 * shared with a service account).
 */
var FOLDER_NAME = 'birds_today_feedback';
var FOLDER_ID = '';        // optional: use this exact folder instead of the name
var MAX_BYTES = 4096;      // a vote is ~300 bytes; anything larger is not ours

function folder_() {
  if (FOLDER_ID) return DriveApp.getFolderById(FOLDER_ID);
  var it = DriveApp.getFoldersByName(FOLDER_NAME);
  return it.hasNext() ? it.next() : DriveApp.createFolder(FOLDER_NAME);
}

function doPost(e) {
  try {
    var body = (e && e.postData && e.postData.contents) || '';
    if (!body || body.length > MAX_BYTES) return text_('ignored');
    var vote = JSON.parse(body);            // throws on anything but JSON
    if (!vote || !vote.image || !vote.vote) return text_('ignored');

    // One file per vote: no read-modify-write, so concurrent votes can't
    // overwrite each other, and the reader can process and move them one by
    // one. The timestamp prefix keeps the folder readable, and the client id
    // plus a random suffix keeps names unique.
    var stamp = Utilities.formatDate(new Date(), 'UTC', "yyyyMMdd'T'HHmmss");
    var name = stamp + '_' + String(vote.vote).slice(0, 8) + '_' +
               String(vote.species || 'unknown').replace(/[^A-Za-z0-9_-]/g, '') + '_' +
               Utilities.getUuid().slice(0, 8) + '.json';
    vote.received = new Date().toISOString();
    folder_().createFile(name, JSON.stringify(vote), 'application/json');
    return text_('ok');
  } catch (err) {
    return text_('error');                  // never leak details to the caller
  }
}

function doGet() {
  var f = folder_();
  return text_('bird feedback sink ready; writing to "' + f.getName() + '" (' +
               f.getId() + ')');
}

function text_(s) {
  return ContentService.createTextOutput(s).setMimeType(ContentService.MimeType.TEXT);
}
