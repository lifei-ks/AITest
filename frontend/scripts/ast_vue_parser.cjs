/**
 * Vue SFC / router AST dumper.
 *
 * Uses @vue/compiler-sfc + @vue/compiler-dom + @babel/parser.
 * Output is JSON consumed by app/analyzer/ast_vue.py.
 */
const fs = require('fs')
const path = require('path')
const { parse } = require('@vue/compiler-sfc')
const { parse: parseTemplate } = require('@vue/compiler-dom')
const babel = require('@babel/parser')

const ELEMENT = 1
const TEXT = 2
const ATTRIBUTE = 6
const DIRECTIVE = 7

function attr(node, name) {
  for (const prop of node.props || []) {
    if (prop.type === ATTRIBUTE && prop.name === name) {
      return prop.value ? String(prop.value.content || '') : ''
    }
    if (prop.type === DIRECTIVE && prop.name === 'bind' && prop.arg && prop.arg.content === name) {
      return prop.exp ? String(prop.exp.content || '') : ''
    }
  }
  return ''
}

function directive(node, name, arg) {
  for (const prop of node.props || []) {
    if (prop.type !== DIRECTIVE || prop.name !== name) continue
    if (arg && (!prop.arg || prop.arg.content !== arg)) continue
    return prop.exp ? String(prop.exp.content || '') : ''
  }
  return ''
}

function textOf(node) {
  if (!node) return ''
  if (node.type === TEXT) return String(node.content || '')
  if (Array.isArray(node.children)) {
    return node.children.map(textOf).join('')
  }
  return ''
}

function descendants(node, tag, out = []) {
  if (!node) return out
  if (node.type === ELEMENT && node.tag === tag) out.push(node)
  for (const child of node.children || []) descendants(child, tag, out)
  return out
}

function collectMessages(source) {
  if (!source) return []
  let ast
  try {
    ast = babel.parse(source, {
      sourceType: 'module',
      plugins: ['typescript', 'jsx', 'decorators-legacy'],
    })
  } catch {
    return []
  }
  const messages = []
  const visit = (node) => {
    if (!node || typeof node !== 'object') return
    if (node.type === 'CallExpression' && node.callee) {
      const callee = node.callee
      if (
        (callee.type === 'MemberExpression' && callee.object && callee.object.name === 'ElMessage') ||
        (callee.type === 'MemberExpression' && callee.object && callee.object.name === 'ElMessageBox')
      ) {
        const arg = (node.arguments || [])[0]
        if (arg && (arg.type === 'StringLiteral' || arg.type === 'Literal')) {
          messages.push(String(arg.value || ''))
        }
      }
    }
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key === 'start' || key === 'end' || key === 'leadingComments' || key === 'trailingComments') continue
      const value = node[key]
      if (Array.isArray(value)) value.forEach(visit)
      else if (value && typeof value === 'object') visit(value)
    }
  }
  visit(ast)
  return messages.filter(Boolean)
}

function parseVue(file, source) {
  const parsed = parse(source, { filename: file })
  const descriptor = parsed.descriptor || {}
  const templateSource = descriptor.template ? String(descriptor.template.content || '') : ''
  const root = templateSource ? parseTemplate(templateSource, { comments: false }) : null
  const forms = []
  const selectors = []
  const uiTexts = []
  const name = path.basename(file, '.vue')

  for (const formNode of descendants(root, 'el-form')) {
    const fields = []
    for (const item of descendants(formNode, 'el-form-item')) {
      const label = attr(item, 'label')
      const prop = attr(item, 'prop')
      const inputs = [
        ...descendants(item, 'el-input'),
        ...descendants(item, 'el-select'),
        ...descendants(item, 'el-switch'),
      ]
      const input = inputs[0] || null
      const placeholder = input ? attr(input, 'placeholder') : ''
      const model = input ? directive(input, 'model') : ''
      const required = item.props?.some((p) => p.type === ATTRIBUTE && p.name === 'required') || false
      let type = 'input'
      if (input && input.tag === 'el-select') type = 'select'
      else if (input && input.tag === 'el-switch') type = 'switch'
      else if (input && /textarea/i.test(String(attr(input, 'type') || ''))) type = 'textarea'
      if (label || prop || placeholder) {
        fields.push({
          label,
          prop,
          type,
          required,
          placeholder,
          model,
        })
        if (placeholder) selectors.push({ type: 'placeholder', value: placeholder })
        else if (label) selectors.push({ type: 'label', value: label })
      }
    }
    forms.push({ fields })
  }

  for (const node of descendants(root, 'el-button')) {
    const text = textOf(node).trim()
    if (text) uiTexts.push(text)
  }
  for (const node of descendants(root, 'button')) {
    const text = textOf(node).trim()
    if (text) uiTexts.push(text)
  }

  const script = `${descriptor.script ? descriptor.script.content : ''}\n${descriptor.scriptSetup ? descriptor.scriptSetup.content : ''}`
  uiTexts.push(...collectMessages(script))

  return {
    kind: 'vue',
    file,
    name,
    forms,
    selectors,
    uiTexts: [...new Set(uiTexts)],
    errors: (parsed.errors || []).map((e) => String(e.message || e)),
  }
}

function parseRouter(file, source) {
  let ast
  try {
    ast = babel.parse(source, {
      sourceType: 'module',
      plugins: ['typescript', 'jsx', 'decorators-legacy'],
    })
  } catch (e) {
    return { kind: 'router', file, routes: [], errors: [String(e.message || e)] }
  }
  const routes = []
  const visit = (node) => {
    if (!node || typeof node !== 'object') return
    if (node.type === 'ObjectExpression') {
      const props = Object.fromEntries(
        (node.properties || [])
          .filter((p) => p.type === 'ObjectProperty' && p.key)
          .map((p) => [p.key.name || p.key.value, p.value]),
      )
      if (props.path && props.path.type === 'StringLiteral') {
        const componentText = props.component ? source.slice(props.component.start, props.component.end) : ''
        const match = componentText.match(/([^/'"]+)\.vue/)
        routes.push({
          path: String(props.path.value || ''),
          component: match ? match[1] : '',
          name: props.name && props.name.type === 'StringLiteral' ? String(props.name.value || '') : '',
        })
      }
    }
    for (const key of Object.keys(node)) {
      if (key === 'loc' || key === 'start' || key === 'end' || key === 'leadingComments' || key === 'trailingComments') continue
      const value = node[key]
      if (Array.isArray(value)) value.forEach(visit)
      else if (value && typeof value === 'object') visit(value)
    }
  }
  visit(ast)
  return { kind: 'router', file, routes, errors: [] }
}

function main() {
  const files = process.argv.slice(2)
  const out = []
  for (const file of files) {
    try {
      const source = fs.readFileSync(file, 'utf8')
      if (file.endsWith('.vue')) out.push(parseVue(file, source))
      else if (/(^|[\\/])(index|router)\.js$/.test(file)) out.push(parseRouter(file, source))
    } catch (e) {
      out.push({ kind: 'error', file, errors: [String(e.message || e)] })
    }
  }
  process.stdout.write(JSON.stringify(out))
}

main()
