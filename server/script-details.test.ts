import assert from 'node:assert/strict'
import test from 'node:test'
import { getDefaultScriptDetails, KNOWN_SCRIPT_FILENAMES } from './script-details.js'
import { taskDetailsSchema } from './validation.js'

test('provides a formatted description of at most 500 characters for every known crawler', () => {
  assert.equal(KNOWN_SCRIPT_FILENAMES.length, 22)
  for (const filename of KNOWN_SCRIPT_FILENAMES) {
    const details = getDefaultScriptDetails(filename)
    assert.ok(details.length > 0, `${filename} should have details`)
    assert.ok(details.length <= 500, `${filename} details exceed 500 characters`)
    for (const section of ['功能：', '覆盖：', '核心逻辑：', '使用：', '注意：']) {
      assert.ok(details.includes(section), `${filename} is missing ${section}`)
    }
  }
})

test('matches crawler filenames from Windows and macOS paths', () => {
  assert.equal(
    getDefaultScriptDetails('C:\\crawlers\\example.py'),
    getDefaultScriptDetails('/Users/example/crawlers/example.py'),
  )
})

test('enforces the 500-character task details limit', () => {
  assert.equal(taskDetailsSchema.safeParse({ details: 'a'.repeat(500) }).success, true)
  assert.equal(taskDetailsSchema.safeParse({ details: 'a'.repeat(501) }).success, false)
})
