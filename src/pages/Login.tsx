import { useState } from 'react'
import { useApp } from '../store/AppStore'
import type { Role } from '../types'
import { Card } from '../components/ui'

export function Login() {
  const { login } = useApp()
  const [name, setName] = useState('Operator')
  const [role, setRole] = useState<Role>('supervisor')

  return (
    <div style={{ minHeight: '100vh', display: 'grid', placeItems: 'center', padding: 24 }}>
      <Card className="stack" title="PackTug Control Panel" hint="Role-based access">
        <div className="field">
          <label>Name</label>
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </div>
        <div className="field">
          <label>Role</label>
          <select value={role} onChange={(e) => setRole(e.target.value as Role)}>
            <option value="operator">operator</option>
            <option value="supervisor">supervisor</option>
            <option value="maintenance">maintenance</option>
          </select>
        </div>
        <button className="btn primary" onClick={() => login(name || 'Operator', role)} style={{ marginTop: 6 }}>
          Sign in
        </button>
        <p className="faint" style={{ fontSize: 11.5, margin: 0 }}>
          Mock auth — the real endpoint is POST /api/auth/login.
        </p>
      </Card>
    </div>
  )
}
