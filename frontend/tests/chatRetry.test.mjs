import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

// Execute the current component/event handler and API wrapper without a browser,
// a live backend, new test dependencies, or any external fetch implementation.
function loadModule(relativePath, imports, globals = {}) {
  const source = readFileSync(new URL(relativePath, import.meta.url), 'utf8');
  const code = ts.transpileModule(source, { compilerOptions: {
    module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX,
  } }).outputText;
  const module = { exports: {} };
  runInNewContext(code, {
    module, exports: module.exports, ...globals,
    require(name) {
      assert.ok(Object.hasOwn(imports, name), `Unexpected import: ${name}`);
      return imports[name];
    },
  });
  return module.exports;
}

function harness(fetchReply, { savedStage = true, initialMessages } = {}) {
  const posts = [];
  const updates = [];
  const hookValues = [];
  let cursor = 0;
  const react = {
    useEffect() {}, // No mount/status calls are needed to exercise this button.
    useRef(initial) {
      const index = cursor++;
      hookValues[index] ??= { current: initial };
      return hookValues[index];
    },
    useState(initial) {
      const index = cursor++;
      if (!(index in hookValues)) hookValues[index] = initial;
      return [hookValues[index], (next) => {
        hookValues[index] = typeof next === 'function' ? next(hookValues[index]) : next;
      }];
    },
  };
  const api = loadModule('../src/lib/api.ts', {}, {
    process: { env: { NEXT_PUBLIC_API_URL: 'http://mock-backend/api/v1' } },
    async fetch(url, options) {
      posts.push({ url, options });
      return fetchReply();
    },
  });
  const handoff = loadModule('../src/lib/documentHandoff.ts', {});
  const jsx = (type, props) => ({ type, props });
  const { default: ChatInterface } = loadModule('../src/components/ChatInterface.tsx', {
    react, 'react/jsx-runtime': { jsx, jsxs: jsx },
    'lucide-react': new Proxy({}, { get: () => () => null }),
    '@/lib/api': api, '@/lib/documentHandoff': handoff,
  }, { console: { warn() {} } });

  function render() {
    cursor = 0;
    return ChatInterface({
      initialCaseId: 'synthetic-case',
      initialMessages: initialMessages ?? [
        { id: 'original-user', sender: 'user', text: 'My landlord has not returned my deposit.' },
        { id: 'bot', sender: 'bot', text: 'Local fallback', quick_replies: ['Try Groq again'],
          retry_action: savedStage ? { message_id: 'bot', label: 'Try Groq again' } : null },
      ],
      onProfileUpdated: (profile) => updates.push(profile), onOpenUploadModal() {},
      onTriggerDocumentModal() { assert.fail('A retry must not trigger document generation'); },
    });
  }
  return { render, posts, updates, messages: () => hookValues[0] };
}

function retryButton(node) {
  if (!node) return undefined;
  if (Array.isArray(node)) return node.map(retryButton).find(Boolean);
  if (node.type === 'button' && node.props.children === 'Try Groq again') return node;
  return retryButton(node.props?.children);
}

function element(node, type) {
  if (!node) return undefined;
  if (Array.isArray(node)) return node.map((item) => element(item, type)).find(Boolean);
  if (node.type === type) return node;
  return element(node.props?.children, type);
}

function response(mode) {
  return {
    ok: true,
    async json() {
      return {
        case_profile: { case_id: 'synthetic-case' }, message_id: 'bot', reply_text: 'Local response',
        llm_mode: mode, llm_provider: 'groq', llm_model: 'openai/gpt-oss-120b',
        quick_replies: mode === 'limited_demo' ? ['Try Groq again'] : [],
        retry_action: mode === 'limited_demo' ? { message_id: 'bot', label: 'Try Groq again' } : null,
      };
    },
  };
}

const settle = () => new Promise((resolve) => setImmediate(resolve));

for (const mode of ['groq', 'limited_demo']) {
  test(`one Try Groq again click sends one POST, including after ${mode} response`, async () => {
    const app = harness(() => response(mode));
    const button = retryButton(app.render());
    assert.equal(button.props.type, 'button'); // Does not also submit the composer form.
    assert.equal(button.props.disabled, false);
    button.props.onClick();
    await settle();
    app.render();
    await settle();

    assert.equal(app.posts.length, 1);
    assert.equal(app.posts[0].url, 'http://mock-backend/api/v1/chat/retry');
    assert.equal(app.posts[0].options.method, 'POST');
    const body = JSON.parse(app.posts[0].options.body);
    assert.deepEqual(body, { case_id: 'synthetic-case', message_id: 'bot' });
    assert.equal(app.messages().length, 2);
    assert.equal(app.messages()[0].text, 'My landlord has not returned my deposit.');
    assert.equal(app.messages()[0].id, 'original-user');
    assert.equal(app.messages()[1].id, 'bot');
    assert.equal(app.messages()[1].text, 'Local response');
    assert.ok(!app.messages().some((item) => item.sender === 'user' && item.text === 'Try Groq again'));
    assert.equal(app.updates.length, 1);
  });
}

test('retry buttons and handler are guarded while the current POST is in flight', async () => {
  let finish;
  const pendingResponse = new Promise((resolve) => { finish = resolve; });
  const app = harness(() => pendingResponse);
  const firstButton = retryButton(app.render());
  firstButton.props.onClick();
  firstButton.props.onClick(); // Same tick, before React has rendered the busy state.
  const busyButton = retryButton(app.render());
  assert.equal(busyButton.props.disabled, true);
  busyButton.props.onClick();
  assert.equal(app.posts.length, 1);
  finish(response('limited_demo'));
  await settle();
  assert.equal(retryButton(app.render()).props.disabled, false);
  assert.equal(app.posts.length, 1);
});

test('a failed retry POST is not automatically resubmitted or added to conversation', async () => {
  const app = harness(() => ({ ok: false, status: 503, async json() { return { detail: 'Local failure' }; } }));
  retryButton(app.render()).props.onClick();
  await settle();
  app.render();
  await settle();
  assert.equal(app.posts.length, 1);
  assert.equal(app.updates.length, 0);
  assert.equal(app.messages().length, 2);
  assert.equal(app.messages()[1].text, 'Local fallback');
});

test('legacy retry labels without a saved stage cannot become conversational text', async () => {
  const app = harness(() => response('groq'), { savedStage: false });
  const button = retryButton(app.render());
  assert.equal(button.props.disabled, true);
  button.props.onClick();
  await settle();
  assert.equal(app.posts.length, 0);
  assert.equal(app.messages().length, 2);
});

test('a new failed chat response exposes a stage retry without resending user history', async () => {
  let replies = 0;
  const app = harness(() => response(replies++ === 0 ? 'limited_demo' : 'groq'), { initialMessages: [] });
  element(app.render(), 'textarea').props.onChange({ target: { value: 'My landlord has not returned my deposit.' } });
  element(app.render(), 'form').props.onSubmit({ preventDefault() {} });
  await settle();
  assert.equal(app.posts.length, 1);
  assert.equal(app.posts[0].url, 'http://mock-backend/api/v1/chat/message');
  assert.equal(JSON.parse(app.posts[0].options.body).message, 'My landlord has not returned my deposit.');
  retryButton(app.render()).props.onClick();
  await settle();
  assert.equal(app.posts.length, 2);
  assert.equal(app.posts[1].url, 'http://mock-backend/api/v1/chat/retry');
  assert.deepEqual(JSON.parse(app.posts[1].options.body), { case_id: 'synthetic-case', message_id: 'bot' });
  assert.equal(app.messages().length, 2);
  assert.equal(app.messages()[0].text, 'My landlord has not returned my deposit.');
  assert.equal(app.messages()[1].text, 'Local response');
});

test('retry control preserves an unsent draft in the composer', async () => {
  const app = harness(() => response('groq'));
  element(app.render(), 'textarea').props.onChange({ target: { value: 'An unsent follow-up' } });
  retryButton(app.render()).props.onClick();
  await settle();
  assert.equal(element(app.render(), 'textarea').props.value, 'An unsent follow-up');
  assert.equal(app.messages().length, 2);
  assert.equal(app.posts.length, 1);
});
