import { useId, useState, type FormEvent, type ReactNode } from 'react'
import { Icon, type IconName } from '../panels/Common'

export function InlineCommandComposer({ label, placeholder, buttonLabel, icon, submitting, onSubmit }: {
  readonly label: string
  readonly placeholder: string
  readonly buttonLabel: string
  readonly icon: IconName
  readonly submitting: boolean
  readonly onSubmit: (value: string) => Promise<boolean>
}): ReactNode {
  const inputId = useId()
  const [value, setValue] = useState('')
  async function submit(event: FormEvent): Promise<void> {
    event.preventDefault()
    const next = value.trim()
    if (next === '' || submitting) return
    if (await onSubmit(next)) setValue('')
  }
  return (
    <form className="inline-command" onSubmit={(event) => void submit(event)}>
      <label htmlFor={inputId}>{label}</label>
      <div>
        <input id={inputId} value={value} onChange={(event) => setValue(event.target.value)} placeholder={placeholder} disabled={submitting} />
        <button type="submit" className="secondary-button" disabled={submitting || value.trim() === ''}>
          <Icon name={icon} size={17} />{buttonLabel}
        </button>
      </div>
    </form>
  )
}
