// Lists the Portuguese text in the dashboard's source and what en.ts does not translate yet.
//   node scripts/i18n-extract.mjs            prints what is missing, exits 1 if anything is
//   node scripts/i18n-extract.mjs --all      prints every string found
// A template literal counts as a pattern: `${n} sessões` is the key "{0} sessões".
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import ts from 'typescript'

const ROOT = new URL('../src', import.meta.url).pathname
const SKIP_FILES = /(lib\/en\.ts|lib\/i18n\.ts|\.test\.)/
// Attributes and call arguments that hold code, not words.
const CODE_ATTR = new Set(['className', 'to', 'href', 'src', 'id', 'key', 'type', 'role', 'name', 'autoComplete', 'target', 'rel', 'variant', 'size', 'tone', 'value', 'd', 'fill', 'stroke', 'viewBox', 'htmlFor', 'style', 'pattern', 'inputMode', 'accept', 'method', 'mode', 'download', 'xmlns', 'strokeWidth'])
const PT_HINT = /[áàâãéêíóôõúçÁÉÍÓÚÂÊÔÃÕÇ]|\b(?:a|o|de|do|da|para|com|sem|em|um|uma|que|não|nao|os|as|no|na|e|ou|por)\b/
const words = (s) => /\p{L}{2,}/u.test(s)
const looksLikeCode = (s) => /^[a-z0-9_.:/#@?=&%-]+$/.test(s) || /^(?:[\w-]+:)?[\w/.[\]%-]+(?:\s+(?:[\w-]+:)?[\w/.[\]%()-]+)+$/.test(s) && /[-:/[\]]/.test(s) && !PT_HINT.test(s) || /^\/|^https?:|^\.\.?\//.test(s)

function files(dir) {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f)
    return statSync(p).isDirectory() ? files(p) : /\.tsx?$/.test(f) && !SKIP_FILES.test(p) ? [p] : []
  })
}

function attrName(node) {
  for (let n = node.parent; n; n = n.parent) {
    if (ts.isJsxAttribute(n)) return n.name.getText()
    if (ts.isJsxElement(n) || ts.isJsxSelfClosingElement(n)) return null
  }
  return null
}

function collect(path, out) {
  const source = ts.createSourceFile(path, readFileSync(path, 'utf8'), ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const add = (s) => {
    const t = s.replace(/\s+/g, ' ').trim()
    if (t && words(t) && !looksLikeCode(t)) out.add(t)
  }
  const visit = (node) => {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node) || ts.isTypeNode(node)) return
    if (ts.isJsxText(node)) add(node.getText())
    else if (ts.isStringLiteralLike(node) && !ts.isTemplateExpression(node)) {
      const parent = node.parent
      const inIndex = ts.isElementAccessExpression(parent)
      const compared = ts.isBinaryExpression(parent) && [ts.SyntaxKind.EqualsEqualsEqualsToken, ts.SyntaxKind.ExclamationEqualsEqualsToken].includes(parent.operatorToken.kind)
      const key = ts.isPropertyAssignment(parent) && parent.name === node
      const call = ts.isCallExpression(parent) && ts.isPropertyAccessExpression(parent.expression) && /^(get|set|has|includes|startsWith|endsWith|getItem|setItem|removeItem|addEventListener|removeEventListener|dispatchEvent|querySelector|getAttribute|setAttribute|replace|split)$/.test(parent.expression.name.text)
      const attr = attrName(node)
      if (!inIndex && !compared && !key && !call && !(attr && CODE_ATTR.has(attr)) && !/lib\/(api|mic|theme|ui)\.ts/.test(path) && /\s|[áàâãéêíóôõúç]|^[A-ZÁÉÍÓÚ]/.test(node.text)) add(node.text)
    } else if (ts.isTemplateExpression(node)) {
      const attr = attrName(node)
      if (!(attr && CODE_ATTR.has(attr)) && !/lib\/api\.ts/.test(path)) {
        const parts = [node.head.text, ...node.templateSpans.map((s) => s.literal.text)]
        let n = 0
        const key = parts.reduce((acc, p, i) => acc + (i ? `{${n++}}` : '') + p, '')
        if (words(parts.join(' ')) && !looksLikeCode(parts.join(' ').trim())) add(key)
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
}

const found = new Set()
files(ROOT).forEach((f) => collect(f, found))
const en = readFileSync(join(ROOT, 'lib/en.ts'), 'utf8')
const known = new Set([...en.matchAll(/^\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)"|`((?:[^`\\]|\\.)*)`)\s*:/gm)].map((m) => (m[1] ?? m[2] ?? m[3]).replace(/\\(.)/g, '$1')))
const same = new Set(readFileSync(new URL('./i18n-same.txt', import.meta.url), 'utf8').split('\n'))  // names and code: the same in both languages
const missing = [...found].filter((s) => !known.has(s) && !same.has(s)).sort()
if (process.argv.includes('--all')) console.log([...found].sort().join('\n'))
else if (missing.length) {
  console.log(missing.join('\n'))
  console.error(`${missing.length} strings without English of ${found.size}`)
  process.exit(1)
} else console.log(`all ${found.size} strings translated`)
