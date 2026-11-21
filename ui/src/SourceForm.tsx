import { useEffect, useId, useRef, useState, type FormEvent } from 'react'

import { ApiError, describeError } from './api'

export interface SourceFormValues {
  name: string
  url: string
  rss_url: string
}

const FIELDS = [
  { key: 'name', label: 'Name', type: 'text', placeholder: 'Example Threat Research' },
  { key: 'url', label: 'Site URL', type: 'url', placeholder: 'https://research.example.com/' },
  { key: 'rss_url', label: 'RSS feed URL', type: 'url', placeholder: 'https://research.example.com/feed' },
] as const

const FIELD_KEYS = FIELDS.map((field) => field.key)
const EMPTY: SourceFormValues = { name: '', url: '', rss_url: '' }

interface Props {
  label: string
  submitLabel: string
  initial?: SourceFormValues
  onSubmit: (values: SourceFormValues) => Promise<void>
  onCancel?: () => void
  resetOnSuccess?: boolean
}

export function SourceForm({ label, submitLabel, initial = EMPTY, onSubmit, onCancel, resetOnSuccess }: Props) {
  const id = useId()
  const [values, setValues] = useState(initial)
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)

  // Move focus to the first invalid field so its error (aria-describedby) is announced.
  useEffect(() => {
    formRef.current?.querySelector<HTMLInputElement>('[aria-invalid="true"]')?.focus()
  }, [errors])

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setSaving(true)
    setErrors({})
    try {
      await onSubmit(values)
      if (resetOnSuccess) setValues(EMPTY)
    } catch (error) {
      if (!(error instanceof ApiError)) {
        setErrors({ '': `Could not save: ${describeError(error)}` })
        return
      }
      const byField = error.byField(FIELD_KEYS)
      // A 409 means the name maps to a slug that is already taken.
      if (error.status === 409 && byField['']) {
        byField.name = byField['']
        delete byField['']
      }
      setErrors(Object.keys(byField).length ? byField : { '': error.message })
    } finally {
      setSaving(false)
    }
  }

  return (
    <form ref={formRef} className="source-form" aria-label={label} onSubmit={handleSubmit} noValidate>
      {FIELDS.map((field) => {
        const inputId = `${id}-${field.key}`
        const error = errors[field.key]
        return (
          <div className="field" key={field.key}>
            <label htmlFor={inputId}>{field.label}</label>
            <input
              id={inputId}
              type={field.type}
              value={values[field.key]}
              placeholder={field.placeholder}
              aria-invalid={error ? true : undefined}
              aria-describedby={error ? `${inputId}-error` : undefined}
              onChange={(event) => setValues({ ...values, [field.key]: event.target.value })}
            />
            {error && (
              <p className="field-error" id={`${inputId}-error`}>
                {error}
              </p>
            )}
          </div>
        )
      })}
      {errors[''] && (
        <p className="form-error" role="alert">
          {errors['']}
        </p>
      )}
      <div className="form-actions">
        <button type="submit" disabled={saving}>
          {saving ? 'Saving…' : submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="secondary" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  )
}
