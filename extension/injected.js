/**
 * Injected into MAIN world on flow.google.com (and an old pinned labs.google
 * tab) — has access to window.grecaptcha.
 * Used solely for reCAPTCHA solving. Media URLs come from the generation API
 * response directly (agent extracts fifeUrl from data.media[].image), so no
 * TRPC response interception is needed.
 */
const SITE_KEY = '6LdsFiUsAAAAAIjVDZcuLhaHiDn5nnHVXVRQGeMV';

// grecaptcha.enterprise.execute serialises badly when called concurrently —
// two overlapping mints can hand back the same token, and a replayed captcha
// token comes back PUBLIC_ERROR_UNUSUAL_ACTIVITY. Queue them. (From flowkit.)
let captchaMintTail = Promise.resolve();

async function mintCaptcha(pageAction) {
  const previous = captchaMintTail.catch(() => {});
  let release;
  captchaMintTail = new Promise((resolve) => { release = resolve; });
  await previous;
  try {
    await waitForGrecaptcha();
    return await window.grecaptcha.enterprise.execute(SITE_KEY, {
      action: pageAction,
    });
  } finally {
    release();
  }
}

window.addEventListener('GET_CAPTCHA', async ({ detail }) => {
  const { requestId, pageAction } = detail;
  try {
    const token = await mintCaptcha(pageAction);
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, token },
    }));
  } catch (e) {
    window.dispatchEvent(new CustomEvent('CAPTCHA_RESULT', {
      detail: { requestId, error: e.message },
    }));
  }
});

// 10s was optimistic: grecaptcha loads lazily on the rewritten frontend
// and a cold tab regularly needs longer (upstream measured this).
function waitForGrecaptcha(timeout = 22000) {
  return new Promise((resolve, reject) => {
    const start = Date.now();
    const check = () => {
      if (window.grecaptcha?.enterprise?.execute) return resolve();
      if (Date.now() - start > timeout) return reject(new Error('grecaptcha not available'));
      setTimeout(check, 200);
    };
    check();
  });
}
