// ─────────────────────────────────────────────
//  Partner Coverage Update Tool — Code.gs
// ─────────────────────────────────────────────

const BASE_URL = 'https://test-agroex.agrostar.in';

// ── Menu ──────────────────────────────────────
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Partner Tools')
    .addItem('🔐 Login', 'showLoginDialog')
    .addItem('📍 Update Coverage', 'showCoverageDialog')
    .addToUi();
}

// ── Login Dialog ──────────────────────────────
function showLoginDialog() {
  const html = HtmlService.createHtmlOutputFromFile('Login')
    .setWidth(420)
    .setHeight(320)
    .setTitle('Partner Tools — Login');
  SpreadsheetApp.getUi().showModalDialog(html, 'Login');
}

// Called from Login.html via google.script.run
function validateUser(username, password) {
  const url = BASE_URL + '/apigateway/uservalidation/';

  const options = {
    method: 'POST',
    contentType: 'application/json',
    payload: JSON.stringify({ username: username, password: password }),
    muteHttpExceptions: true
  };

  try {
    const response = UrlFetchApp.fetch(url, options);
    const data = JSON.parse(response.getContentText());

    if (data.status === true) {
      // Persist token + user info for subsequent API calls in this session
      const props = PropertiesService.getScriptProperties();
      props.setProperty('AUTH_TOKEN', data.responseData['X-Authorization-Token']);
      props.setProperty('LOGGED_IN_USER', data.responseData.name);
      props.setProperty('LOGGED_IN_USERNAME', data.responseData.username);

      return {
        success: true,
        name: data.responseData.name,
        message: data.message
      };
    } else {
      return {
        success: false,
        message: data.message || 'Invalid credentials. Please try again.'
      };
    }
  } catch (e) {
    return {
      success: false,
      message: 'Network error: ' + e.message
    };
  }
}

// ── Auth Guard — call before any protected operation ──
function getAuthToken() {
  const token = PropertiesService.getScriptProperties().getProperty('AUTH_TOKEN');
  if (!token) {
    SpreadsheetApp.getUi().alert('⚠️ Not logged in. Please use Partner Tools → Login first.');
    return null;
  }
  return token;
}

function getLoggedInUser() {
  return PropertiesService.getScriptProperties().getProperty('LOGGED_IN_USER') || 'Unknown';
}

// ── Placeholder for Step 2 — Coverage Update ──
function showCoverageDialog() {
  const token = getAuthToken();
  if (!token) return; // auth guard blocks further execution
  // Step 2 dialog will be added here
  SpreadsheetApp.getUi().alert('✅ Logged in as: ' + getLoggedInUser() + '\n\nCoverage update UI coming in next step.');
}
