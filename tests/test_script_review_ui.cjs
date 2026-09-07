// Run with: node --test tests/test_script_review_ui.cjs
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const vm = require('node:vm');

function editor(value) {
    const status = {textContent: '', classList: {toggle() {}}};
    const input = {value, events: [], dispatchEvent(event) { this.events.push(event.type); }};
    const card = {
        dataset: {scriptReviewStatus: 'pending'},
        querySelector(selector) {
            return selector === '[name="script_text"]' ? input :
                selector === '[data-script-save-status]' ? status : null;
        },
    };
    const window = {setTimeout() {}, location: {reload() { throw Error('Unexpected reload'); }}};
    vm.runInNewContext(readFileSync('static/js/batches/detail.js', 'utf8'), {
        window, document: {getElementById() { return card; }, body: {addEventListener() {}}}, Event,
    });
    function save(data, successful = true) {
        window.handleScriptSaveResponse({detail: {
            successful, xhr: {responseText: JSON.stringify(successful ? {data} : {message: data})},
        }}, 'post');
    }
    return {input, status, save};
}

test('save reflects server punctuation and updates the live counter', () => {
    const e = editor('Ein vollständiger Text');
    e.save({submitted_script_text: e.input.value, script_text: e.input.value + '.'});
    assert.equal(e.input.value, 'Ein vollständiger Text.');
    assert.deepEqual(e.input.events, ['input']);
    assert.equal(e.status.textContent, 'Saved');
});

test('late save acknowledgement preserves newer typing', () => {
    const e = editor('Newer unsaved text');
    e.save({submitted_script_text: 'Earlier text', script_text: 'Earlier text.'});
    assert.equal(e.input.value, 'Newer unsaved text');
    assert.deepEqual(e.input.events, []);
});

test('saved invalid draft retains the server correction', () => {
    const e = editor('Zu kurz');
    e.save({submitted_script_text: 'Zu kurz', script_text: 'Zu kurz.', validation_error: 'Needs 14-18 words.'});
    assert.equal(e.status.textContent, 'Draft saved. Needs 14-18 words.');
});

test('failed saves display the server error', () => {
    const e = editor('My unsaved text');
    e.save('Save failed: invalid presenter format.', false);
    assert.equal(e.status.textContent, 'Save failed: invalid presenter format.');
    assert.equal(e.input.value, 'My unsaved text');
});
