import { useState, type KeyboardEvent } from 'react'
import { Plus, X } from 'lucide-react'

import { cx } from './ui'

export function TagInput({
  tags,
  onChange,
  placeholder,
  tone = 'cyan',
}: {
  tags: string[]
  onChange: (next: string[]) => void
  placeholder?: string
  tone?: 'cyan' | 'rose'
}) {
  const [value, setValue] = useState('')

  const add = () => {
    const v = value.trim()
    if (!v) return
    if (!tags.some((t) => t.toLowerCase() === v.toLowerCase())) onChange([...tags, v])
    setValue('')
  }
  const remove = (i: number) => onChange(tags.filter((_, idx) => idx !== i))
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter') {
      e.preventDefault()
      add()
    } else if (e.key === 'Backspace' && !value && tags.length) {
      remove(tags.length - 1)
    }
  }

  const chip =
    tone === 'rose'
      ? 'bg-brand-rose/15 text-brand-rose'
      : 'bg-brand-cyan/15 text-brand-cyan'

  return (
    <div>
      <div className="mb-2 flex flex-wrap gap-2">
        {tags.length === 0 && <span className="text-xs text-ghost">（暂无）</span>}
        {tags.map((t, i) => (
          <span key={`${t}-${i}`} className={cx('inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1 text-sm', chip)}>
            {t}
            <button onClick={() => remove(i)} className="opacity-60 transition hover:opacity-100" aria-label="remove">
              <X className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className="input"
          value={value}
          placeholder={placeholder}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKey}
        />
        <button className="btn-ghost shrink-0" onClick={add} type="button">
          <Plus className="h-4 w-4" /> 加
        </button>
      </div>
    </div>
  )
}
