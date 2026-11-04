import { afterAll, expect, test } from 'bun:test';
const previousKey = process.env.ANTHROPIC_API_KEY;
const previousModel = process.env.MODEL_ID;
process.env.ANTHROPIC_API_KEY = 'synthetic-model-config-test-key';
process.env.MODEL_ID = 'claude-sonnet-5-5';
const { model } = await import('./loan-desk');
afterAll(() => {
 if(previousKey===undefined) delete process.env.ANTHROPIC_API_KEY; else process.env.ANTHROPIC_API_KEY=previousKey;
 if(previousModel===undefined) delete process.env.MODEL_ID; else process.env.MODEL_ID=previousModel;
});
for(const configured of ['claude-sonnet-5-5','anthropic:claude-sonnet-5-5','anthropic/claude-sonnet-5-5','  anthropic:claude-sonnet-5-5  ','']) {
 test(`uses a single Mastra provider prefix for ${configured || 'the default model'}`,()=>{
  process.env.MODEL_ID=configured;
  expect(model().id).toBe('anthropic/claude-sonnet-5-5');
 });
}
