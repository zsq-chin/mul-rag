const SENSITIVE_MODEL_KEYS = ['api_key', 'apiKey', 'api_base', 'encrypted_api_key']

export function applyModelSelection(meta, selection) {
  const next = { ...meta }
  for (const key of ['user_model_id', 'model_provider', 'model_name', ...SENSITIVE_MODEL_KEYS]) {
    delete next[key]
  }

  if (selection?.kind === 'user') {
    next.user_model_id = selection.userModelId
  } else if (selection?.kind === 'builtin') {
    if (selection.provider) next.model_provider = selection.provider
    if (selection.name) next.model_name = selection.name
  }

  return next
}
