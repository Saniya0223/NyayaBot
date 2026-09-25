import { test } from 'node:test';
import assert from 'node:assert/strict';
import { helpLevelLabels, helpReasonLabels, helpTriggerLabels } from '../src/lib/professionalHelp.ts';

test('workspace maps assessment codes to readable copy', () => {
  assert.match(helpLevelLabels.LEGAL_HELP_RECOMMENDED, /legal advice/i);
  assert.match(helpReasonLabels.FORMAL_PROCEEDING_STARTED, /proceedings/i);
  assert.match(helpTriggerLabels.BANK_REJECTED_CLAIM, /bank/i);
  assert.equal(helpReasonLabels.SELF_HELP_REASONABLE, undefined);
});
