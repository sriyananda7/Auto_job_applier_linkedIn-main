import os
import re
import time
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.select import Select
from selenium.common.exceptions import WebDriverException
from config.settings import EXTERNAL_DRY_RUN, EXTERNAL_TICK_ALL_CONSENTS, MAX_STEPS, DRY_RUN_VIEW_SECONDS

SUBMIT_WORDS = ('submit application', 'submit my application', 'submit', 'send application', 'apply', 'apply now', 'complete application')
NEXT_WORDS = ('next', 'next step', 'continue', 'save and continue', 'save & continue', 'save and next', 'review')
LANDING_WORDS = ('apply', 'apply now', 'apply for this job', 'apply for this position', 'apply to this job', 'apply for job', 'start application', "i'm interested")
SUCCESS_PHRASES = ('thank you for applying', 'thanks for applying', 'application has been submitted', 'application submitted', 'application received', 'successfully submitted', 'we have received your application', 'thank you for your application', 'your application has been received')
SAFE_CONSENT = re.compile(r'privacy|data processing|personal data|gdpr|data protection', re.I)

_LABEL_JS = """
const el = arguments[0];
let t = el.getAttribute('aria-label') || '';
if (!t && el.id) {
  const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
  if (l) t = l.innerText;
}
if (!t) { const l = el.closest('label'); if (l) t = l.innerText; }
if (!t) {
  const ids = (el.getAttribute('aria-labelledby') || '').split(' ').filter(Boolean);
  t = ids.map(i => { const n = document.getElementById(i); return n ? n.innerText : ''; }).join(' ');
}
if (!t) {
  let p = el.parentElement;
  for (let i = 0; i < 3 && p && !t; i++) {
    const l = p.querySelector('label');
    if (l) t = l.innerText;
    p = p.parentElement;
  }
}
if (!t) t = el.getAttribute('placeholder') || el.getAttribute('name') || '';
return (t || '').trim();
"""

_GROUP_JS = """
const el = arguments[0];
const fs = el.closest('fieldset');
if (fs) { const lg = fs.querySelector('legend'); if (lg && lg.innerText.trim()) return lg.innerText.trim(); }
let p = el.parentElement;
for (let i = 0; i < 6 && p; i++) {
  if (p.querySelectorAll('input[type=radio]').length >= 2) {
    const t = (p.innerText || '').trim();
    if (t) return t.split('\\n')[0];
  }
  p = p.parentElement;
}
return '';
"""

_VISIBLE_JS = "return !!(arguments[0].offsetWidth || arguments[0].offsetHeight || arguments[0].getClientRects().length);"

def _label_for(driver, el):
    try:
        return driver.execute_script(_LABEL_JS, el) or ''
    except WebDriverException:
        return ''

def _parent_visible(driver, el):
    try:
        return bool(driver.execute_script(_VISIBLE_JS, el.find_element(By.XPATH, '..')))
    except WebDriverException:
        return False

def _js_click(driver, el):
    driver.execute_script("arguments[0].scrollIntoView({block:'center'}); arguments[0].click();", el)

def _is_required(el, label):
    return bool(el.get_attribute('required')) or el.get_attribute('aria-required') == 'true' or label.strip().endswith('*')

def _find_button(driver, words, xp="//button | //input[@type='submit' or @type='button'] | //a[@role='button']"):
    for b in driver.find_elements(By.XPATH, xp):
        try:
            if not b.is_displayed() or not b.is_enabled():
                continue
            text = (b.text or b.get_attribute('value') or b.get_attribute('aria-label') or '').strip().lower()
            if text in words:
                return b
        except WebDriverException:
            pass
    return None

def _visible_field_count(driver):
    n = 0
    for el in driver.find_elements(By.CSS_SELECTOR, "input[type=text], input[type=email], textarea, input[type=file]"):
        try:
            if el.is_displayed() or el.get_attribute('type') == 'file':
                n += 1
        except WebDriverException:
            pass
    return n

def _enter_form(driver):
    if _visible_field_count(driver) >= 3:
        return
    before = set(driver.window_handles)
    btn = _find_button(driver, LANDING_WORDS, "//button | //a | //input[@type='submit']")
    if not btn:
        return
    try:
        _js_click(driver, btn)
    except WebDriverException:
        return
    time.sleep(4)
    new = [h for h in driver.window_handles if h not in before]
    if new:
        driver.switch_to.window(new[-1])
        time.sleep(2)

def _blocker(driver):
    for f in driver.find_elements(By.XPATH, "//iframe[contains(@src,'captcha') or contains(@src,'turnstile')]"):
        try:
            if f.is_displayed() and f.size.get('height', 0) > 100:
                return 'manual: captcha'
        except WebDriverException:
            pass
    for p in driver.find_elements(By.CSS_SELECTOR, "input[type=password]"):
        try:
            if p.is_displayed():
                return 'manual: needs an account / login'
        except WebDriverException:
            pass
    return None

def _submitted(driver, wait=10):
    end = time.time() + wait
    while time.time() < end:
        try:
            body = driver.find_element(By.TAG_NAME, 'body').text.lower()
            url = driver.current_url.lower()
        except WebDriverException:
            return False
        if any(p in body for p in SUCCESS_PHRASES) or any(k in url for k in ('thank', 'confirmation', 'submitted', 'success')):
            return True
        time.sleep(1)
    return False

def _fill_page(driver, text_answer, choice_answer, resume_path):
    missing = []
    uploaded = False
    for f in driver.find_elements(By.CSS_SELECTOR, "input[type=file]"):
        try:
            if uploaded or 'cover' in _label_for(driver, f).lower():
                continue
            uploaded = True
            if f.get_attribute('value'):
                continue
            if resume_path and os.path.exists(resume_path):
                f.send_keys(os.path.abspath(resume_path))
                time.sleep(2)
            elif _is_required(f, ''):
                missing.append('resume file not found at ' + str(resume_path))
        except WebDriverException:
            pass

    css = "input:not([type]), input[type=text], input[type=email], input[type=tel], input[type=url], input[type=number], textarea"
    for el in driver.find_elements(By.CSS_SELECTOR, css):
        try:
            if not el.is_displayed() or not el.is_enabled() or el.get_attribute('value'):
                continue
            label = _label_for(driver, el)
            ans = text_answer(label.lower())
            if ans in (None, ''):
                if _is_required(el, label):
                    missing.append(label[:60] or 'unlabelled field')
                continue
            try:
                el.click()
            except WebDriverException:
                pass
            el.send_keys(str(ans))
            if el.get_attribute('role') == 'combobox':
                time.sleep(1)
                el.send_keys(Keys.ENTER)
        except WebDriverException:
            pass

    for s in driver.find_elements(By.TAG_NAME, 'select'):
        try:
            if not s.is_displayed():
                continue
            sel = Select(s)
            current = sel.first_selected_option.text.strip().lower()
            if current and not current.startswith(('select', 'please', '--', 'choose')):
                continue
            label = _label_for(driver, s)
            idx = choice_answer(label.lower(), [o.text.strip() for o in sel.options])
            if idx is None:
                if _is_required(s, label):
                    missing.append(label[:60] or 'dropdown')
                continue
            sel.select_by_index(idx)
        except WebDriverException:
            pass

    groups = {}
    for r in driver.find_elements(By.CSS_SELECTOR, "input[type=radio]"):
        groups.setdefault(r.get_attribute('name') or r.get_attribute('id'), []).append(r)
    for radios in groups.values():
        try:
            if any(r.is_selected() for r in radios) or not _parent_visible(driver, radios[0]):
                continue
            question = driver.execute_script(_GROUP_JS, radios[0]) or ''
            idx = choice_answer(question.lower(), [_label_for(driver, r) for r in radios])
            if idx is None:
                missing.append(question[:60] or 'radio question')
                continue
            _js_click(driver, radios[idx])
        except WebDriverException:
            pass

    for c in driver.find_elements(By.CSS_SELECTOR, "input[type=checkbox]"):
        try:
            if c.is_selected() or not _parent_visible(driver, c):
                continue
            label = _label_for(driver, c)
            if EXTERNAL_TICK_ALL_CONSENTS or SAFE_CONSENT.search(label):
                _js_click(driver, c)
            elif _is_required(c, label):
                missing.append('checkbox: ' + label[:50])
        except WebDriverException:
            pass

    return missing

def run_external_filler(driver, text_answer, choice_answer, resume_path):
    try:
        _enter_form(driver)
        for _ in range(MAX_STEPS):
            blocked = _blocker(driver)
            if blocked:
                return blocked
            missing = _fill_page(driver, text_answer, choice_answer, resume_path)
            if missing:
                return 'manual: no answer for ' + '; '.join(missing[:3])
            submit = _find_button(driver, SUBMIT_WORDS)
            if submit:
                if EXTERNAL_DRY_RUN:
                    time.sleep(DRY_RUN_VIEW_SECONDS)
                    return 'dry-run: filled, stopped before submit'
                _js_click(driver, submit)
                if _submitted(driver):
                    return 'submitted'
                return _blocker(driver) or 'manual: submit not confirmed'
            nxt = _find_button(driver, NEXT_WORDS)
            if not nxt:
                return 'manual: no submit button found'
            _js_click(driver, nxt)
            time.sleep(3)
        return 'manual: too many steps'
    except Exception as e:
        return f'failed: {type(e).__name__}'