import { useEffect, useRef, useState, type PointerEvent } from 'react'
import {
  api,
  ARM_SEQUENCE_GRIPPER_CLOSE,
  ARM_SEQUENCE_GRIPPER_OPEN,
  type ArmPose,
  type ArmPosePreset,
  type ArmSequence,
  type ArmStatus,
  type CageFitResult,
  type CageGeometry,
  type ExecutePickResult,
  type HandEyeCalibration,
  type PickPathResult,
  type PickPathVec3,
  type QrDetection,
} from '../services/api'
import { Badge, Card, PageHeader } from '../components/ui'
import { clock } from '../lib/format'
import { ARM_TARGET_STORAGE_KEY, placementOrientation, type ArmTargetFromPacking } from '../lib/digitalTwin'
import { CAGE_BIN } from '../data/mock'

const JOINTS = [
  { id: 'J1', name: 'base' },
  { id: 'J2', name: 'shoulder' },
  { id: 'J3', name: 'elbow' },
  { id: 'J4', name: 'wrist 1' },
  { id: 'J5', name: 'wrist 2' },
  { id: 'J6', name: 'wrist 3' },
]

const HOSTS = [
  { label: 'WiFi hotspot', ip: '10.10.10.10' },
  { label: 'Ethernet', ip: '169.254.200.200' },
  { label: 'Jetson relay', ip: '127.0.0.1' },
]

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))

const isGripperStep = (step: string) =>
  step === ARM_SEQUENCE_GRIPPER_OPEN || step === ARM_SEQUENCE_GRIPPER_CLOSE

const sequenceStepLabel = (step: string) =>
  step === ARM_SEQUENCE_GRIPPER_OPEN
    ? 'Gripper: open'
    : step === ARM_SEQUENCE_GRIPPER_CLOSE
      ? 'Gripper: close'
      : step

const POSE_FIELDS: Array<{ key: keyof ArmPose; label: string; step: number }> = [
  { key: 'x', label: 'X (m)', step: 0.01 },
  { key: 'y', label: 'Y (m)', step: 0.01 },
  { key: 'z', label: 'Z (m)', step: 0.01 },
  { key: 'roll', label: 'Roll (°)', step: 1 },
  { key: 'pitch', label: 'Pitch (°)', step: 1 },
  { key: 'yaw', label: 'Yaw (°)', step: 1 },
]

const CORNER_LABELS = [
  'FL bottom',
  'FR bottom',
  'BR bottom',
  'BL bottom',
  'FL top',
  'FR top',
  'BR top',
  'BL top',
]

const CAGE_STORAGE_KEY = 'packtug.cageGeometry'

const DEFAULT_CAGE: CageGeometry = {
  x: 0.25,
  y: 0,
  z: 0,
  yaw: -90,
  width: CAGE_BIN.w / 1000,
  depth: CAGE_BIN.d / 1000,
  height: CAGE_BIN.h / 1000,
  opening_margin: 0.02,
  clearance_m: 0.03,
  front_top_open: true,
}

const DEFAULT_IGNORED = [false, false, false, false, false, false, true, true]

type SavedCageTeaching = {
  corners: Array<PickPathVec3 | null>
  ignored: boolean[]
  cage: CageGeometry
  fit: CageFitResult | null
  lastStart?: ArmPose | null
  home?: ArmPose | null
  homeRecorded?: boolean
}

function loadCageTeaching(): SavedCageTeaching | null {
  try {
    const raw = localStorage.getItem(CAGE_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as SavedCageTeaching
    if (!Array.isArray(parsed.corners) || parsed.corners.length !== 8) return null
    if (!Array.isArray(parsed.ignored) || parsed.ignored.length !== 8) return null
    return parsed
  } catch {
    return null
  }
}

function NumberField({
  label,
  value,
  step = 0.01,
  onChange,
}: {
  label: string
  value: number
  step?: number
  onChange: (value: number) => void
}) {
  return (
    <label className="field" style={{ minWidth: 78 }}>
      <span style={{ fontSize: 10.5, color: 'var(--text-dim)' }}>{label}</span>
      <input
        type="number"
        step={step}
        value={value}
        onChange={(event) => {
          const next = Number(event.target.value)
          if (Number.isFinite(next)) onChange(next)
        }}
        style={{ width: 84 }}
      />
    </label>
  )
}

function PoseEditor({
  title,
  pose,
  onChange,
  positionOnly = false,
}: {
  title: string
  pose: ArmPose
  onChange: (pose: ArmPose) => void
  positionOnly?: boolean
}) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      <span className="muted" style={{ fontSize: 12 }}>
        {title}
      </span>
      <div className="row" style={{ gap: 6 }}>
        {POSE_FIELDS.filter((field) => !positionOnly || ['x', 'y', 'z'].includes(field.key)).map((field) => (
          <NumberField
            key={field.key}
            label={field.label}
            step={field.step}
            value={pose[field.key]}
            onChange={(value) => onChange({ ...pose, [field.key]: value } as ArmPose)}
          />
        ))}
      </div>
    </div>
  )
}

type LogEntry = { id: string; t: string; text: string; level: 'info' | 'success' | 'warn' | 'error' }
type DepthStats = {
  nearest_m: number | null
  farthest_m: number | null
  valid_pixels: number
  age_sec: number | null
}

export function Arm() {
  const [status, setStatus] = useState<ArmStatus | null>(null)
  const [targets, setTargets] = useState(['0', '0', '0', '0', '0', '0'])
  const [step, setStep] = useState(2)
  const [pending, setPending] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [log, setLog] = useState<LogEntry[]>([])
  const [host, setHost] = useState(() => localStorage.getItem('packtug.armHost') ?? '10.10.10.10')
  const [confirmCalibration, setConfirmCalibration] = useState(false)
  const [confirmRelease, setConfirmRelease] = useState(false)
  const [poses, setPoses] = useState<ArmPosePreset[]>([])
  const [poseName, setPoseName] = useState('')
  const [sequences, setSequences] = useState<ArmSequence[]>([])
  const [sequenceName, setSequenceName] = useState('')
  const [dwellSec, setDwellSec] = useState(1)
  const [transitPose, setTransitPose] = useState('')
  const [draftSteps, setDraftSteps] = useState<string[]>([])
  const [stepToAdd, setStepToAdd] = useState('')
  const [sequenceRun, setSequenceRun] = useState<
    { name: string; step: number; total: number; phase: 'transit' | 'pose' | 'gripper' } | null
  >(null)
  const [sequenceStopping, setSequenceStopping] = useState(false)
  const [linearPath, setLinearPath] = useState(true)
  const [controlMode, setControlMode] = useState<'joint' | 'linear'>('linear')
  const [linearSliders, setLinearSliders] = useState({ x: 0, y: 0, z: 0 })
  const [orientationPad, setOrientationPad] = useState({ x: 0, y: 0 })
  const [j6Target, setJ6Target] = useState(0)
  const [linearSpeedScale, setLinearSpeedScale] = useState(4)
  const canMove =
    !!status?.connected &&
    !status.calibration_needed &&
    !status.learning_mode &&
    !status.busy &&
    !status.fault &&
    !status.hardware_motion_fault &&
    !pending
  const [camKey, setCamKey] = useState(0)
  const [camError, setCamError] = useState(false)
  const [camMode, setCamMode] = useState<'rgb' | 'depth'>('rgb')
  const [camSwitching, setCamSwitching] = useState(false)
  const [camTargetMode, setCamTargetMode] = useState<'rgb' | 'depth' | null>(null)
  const [depthStats, setDepthStats] = useState<DepthStats | null>(null)
  const [qr, setQr] = useState<QrDetection | null>(null)
  const [qrPending, setQrPending] = useState(false)
  const [qrError, setQrError] = useState<string | null>(null)
  const [qrTargetOffset, setQrTargetOffset] = useState(() => {
    try {
      const parsed = JSON.parse(localStorage.getItem('packtug.qrTargetOffset') ?? '') as {
        x?: number; y?: number; z?: number
      }
      if ([parsed.x, parsed.y, parsed.z].every((value) => Number.isFinite(value))) {
        return { x: parsed.x!, y: parsed.y!, z: parsed.z! }
      }
    } catch {
      // Use the current paper block's front-label to gripper-web offset.
    }
    return { x: 0, y: 0.0525, z: 0 }
  })
  const [frozenVisionFrame, setFrozenVisionFrame] = useState<string | null>(null)
  const [visionCalib, setVisionCalib] = useState<HandEyeCalibration | null>(null)
  const [visionPending, setVisionPending] = useState(false)
  const [visionMessage, setVisionMessage] = useState<string | null>(null)

  const [savedCage] = useState(() => loadCageTeaching())

  const [pathVia, setPathVia] = useState<ArmPose>({
    x: 0.30,
    y: -0.05,
    z: 0.25,
    roll: 180,
    pitch: 0,
    yaw: 0,
  })
  const [useVia, setUseVia] = useState(false)
  const [useCurrentStart, setUseCurrentStart] = useState(true)
  const [pathStart, setPathStart] = useState<ArmPose>({
    x: 0.10,
    y: 0,
    z: 0.20,
    roll: 180,
    pitch: 0,
    yaw: 90,
  })
  const [lastStart, setLastStart] = useState<ArmPose | null>(
    () => savedCage?.lastStart ?? null,
  )
  const [useHome, setUseHome] = useState(true)
  const [homeRecorded, setHomeRecorded] = useState(
    () => savedCage?.homeRecorded ?? false,
  )
  const [pathHome, setPathHome] = useState<ArmPose>(
    () =>
      savedCage?.home ?? {
        x: 0.30,
        y: 0,
        z: 0.20,
        roll: 180,
        pitch: 0,
        yaw: 0,
      },
  )
  const [pathTarget, setPathTarget] = useState<ArmPose>({
    x: 0.40,
    y: -0.10,
    z: 0.10,
    roll: 180,
    pitch: 0,
    yaw: 0,
  })
  const [pathCage, setPathCage] = useState<CageGeometry>(
    () => savedCage?.cage ?? DEFAULT_CAGE,
  )
  const [pathPayloadRadius, setPathPayloadRadius] = useState(0)
  const [pathApproach, setPathApproach] = useState(0.06)
  const [pathGateClearance, setPathGateClearance] = useState(0.08)
  const [pathResult, setPathResult] = useState<PickPathResult | null>(null)
  const [pathPending, setPathPending] = useState(false)
  const [pathError, setPathError] = useState<string | null>(null)
  const [twinTargetNote, setTwinTargetNote] = useState<string | null>(null)
  const [execRetract, setExecRetract] = useState(0.08)
  const [execHomeLift, setExecHomeLift] = useState(0.08)
  const [returnHomeAfter, setReturnHomeAfter] = useState(false)
  const [execPending, setExecPending] = useState(false)
  const [execResult, setExecResult] = useState<ExecutePickResult | null>(null)
  const [execError, setExecError] = useState<string | null>(null)
  const [cageFront, setCageFront] = useState<ArmPose | null>(null)
  const [cageCenterRecorded, setCageCenterRecorded] = useState(false)
  const [cageMessage, setCageMessage] = useState<string | null>(null)
  const [cageCorners, setCageCorners] = useState<Array<PickPathVec3 | null>>(
    () => savedCage?.corners ?? Array(8).fill(null),
  )
  const [cageFit, setCageFit] = useState<CageFitResult | null>(
    () => savedCage?.fit ?? null,
  )
  const [cageFitPending, setCageFitPending] = useState(false)
  const [cageIgnored, setCageIgnored] = useState<boolean[]>(
    () => savedCage?.ignored ?? [...DEFAULT_IGNORED],
  )
  const [dragRect, setDragRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null)
  const dragStartRef = useRef<{ x: number; y: number } | null>(null)
  const dragFrameRef = useRef<{ dataUrl: string; capturedAt: number } | null>(null)
  const cameraImgRef = useRef<HTMLImageElement | null>(null)
  const lastCamRetryRef = useRef(0)
  const linearSliderRef = useRef({ x: 0, y: 0, z: 0 })
  const keyInputRef = useRef({ x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0, j6: 0 })
  const orientationInputRef = useRef({ pitch: 0, yaw: 0 })
  const orientationActiveRef = useRef(false)
  const jogStreamActiveRef = useRef(false)
  const orientationDraggingRef = useRef(false)
  const j6DraggingRef = useRef(false)
  const stopSequenceRef = useRef(false)

  useEffect(() => {
    const payload: SavedCageTeaching = {
      corners: cageCorners,
      ignored: cageIgnored,
      cage: pathCage,
      fit: cageFit,
      lastStart,
      home: pathHome,
      homeRecorded,
    }
    try {
      localStorage.setItem(CAGE_STORAGE_KEY, JSON.stringify(payload))
    } catch {
      // Ignore storage errors (private mode or quota exceeded).
    }
  }, [cageCorners, cageIgnored, pathCage, cageFit, lastStart, pathHome, homeRecorded])

  useEffect(() => {
    localStorage.setItem('packtug.qrTargetOffset', JSON.stringify(qrTargetOffset))
  }, [qrTargetOffset])

  useEffect(() => {
    try {
      const raw = localStorage.getItem(ARM_TARGET_STORAGE_KEY)
      if (!raw) return
      const target = JSON.parse(raw) as ArmTargetFromPacking
      setPathTarget((current) => ({
        ...current,
        x: target.x,
        y: target.y,
        z: target.z,
        ...placementOrientation(pathCage),
      }))
      setPathPayloadRadius(target.payload_radius_m ?? 0)
      setTwinTargetNote(
        `Packing box #${target.placementSeq} (${target.reference}) → front-face centre ${target.x.toFixed(3)}, ${target.y.toFixed(3)}, ${target.z.toFixed(3)} m; level placement aligned with cage`,
      )
      localStorage.removeItem(ARM_TARGET_STORAGE_KEY)
    } catch {
      setTwinTargetNote(null)
    }
  }, [])

  useEffect(() => {
    let alive = true
    let seeded = false

    async function poll() {
      try {
        const s = await api.getArmStatus()
        if (!alive) return
        setStatus(s)
        setError(s.error)
        if (!seeded && s.connected && s.joints_deg) {
          seeded = true
          setTargets(s.joints_deg.map((v) => v.toFixed(1)))
        }
      } catch (err) {
        if (alive) setError(err instanceof Error ? err.message : String(err))
      }
    }

    void poll()
    const timer = window.setInterval(poll, 1000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let alive = true
    const tick = async () => {
      try {
        const res = await fetch('/api/camera/depth_stats')
        if (!res.ok) return
        const stats = (await res.json()) as DepthStats
        if (alive) setDepthStats(stats)
      } catch {
        // The camera card handles stream errors; stats are optional.
      }
    }
    void tick()
    const timer = window.setInterval(tick, 1000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [])

  useEffect(() => {
    let alive = true
    const check = async () => {
      try {
        const res = await fetch('/api/camera/status')
        if (!res.ok) return
        const health = (await res.json()) as {
          mode?: string
          color?: { age_sec?: number | null }
          depth?: { age_sec?: number | null }
        }
        if (!alive) return
        const sourceMode = health.mode === 'depth' ? 'depth' : health.mode === 'rgb' ? 'rgb' : null
        if (sourceMode && sourceMode !== camMode) {
          setCamMode(sourceMode)
          setCamError(false)
          setCamKey((value) => value + 1)
          lastCamRetryRef.current = Date.now()
          return
        }
        const age = sourceMode === 'depth' ? health.depth?.age_sec : health.color?.age_sec
        if ((age == null || age > 3) && Date.now() - lastCamRetryRef.current > 8000) {
          lastCamRetryRef.current = Date.now()
          setCamError(false)
          setCamKey((value) => value + 1)
        }
      } catch {
        // Camera health polling is best-effort; the image onError handles hard failures.
      }
    }
    void check()
    const timer = window.setInterval(check, 3000)
    return () => {
      alive = false
      window.clearInterval(timer)
    }
  }, [camMode])

  useEffect(() => {
    if (controlMode === 'linear') return
    linearSliderRef.current = { x: 0, y: 0, z: 0 }
    setLinearSliders({ x: 0, y: 0, z: 0 })
    orientationInputRef.current = { pitch: 0, yaw: 0 }
    setOrientationPad({ x: 0, y: 0 })
  }, [controlMode])

  useEffect(() => {
    if (!j6DraggingRef.current && status?.joints_deg?.length) {
      setJ6Target(Math.round(status.joints_deg[5] * 10) / 10)
    }
  }, [status?.joints_deg])

  useEffect(() => {
    if (controlMode !== 'linear') return
    const held = new Set<string>()
    const update = () => {
      const axis = (plus: string, minus: string) =>
        (held.has(plus) ? 1 : 0) - (held.has(minus) ? 1 : 0)
      const zUp = held.has('q')
      const zDown = held.has('e')
      keyInputRef.current = {
        x: axis('d', 'a'),
        y: axis('w', 's'),
        z: (zUp ? 1 : 0) - (zDown ? 1 : 0),
        roll: axis('l', 'j'),
        pitch: axis('i', 'k'),
        yaw: axis('o', 'u'),
        j6: axis('m', 'n'),
      }
    }
    const handleDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
      const key = event.key.toLowerCase()
      if (!['w','a','s','d','q','e','u','i','o','j','k','l','n','m','arrowleft','arrowright','arrowup','arrowdown','pageup','pagedown'].includes(key)) return
      event.preventDefault()
      if (['w','a','s','d','q','e','arrowleft','arrowright','arrowup','arrowdown','pageup','pagedown'].includes(key)) {
        // W/S/A/D/Q/E are translation-only. Drop any sphere orientation that
        // was left behind by a previous drag.
        orientationActiveRef.current = false
        orientationInputRef.current = { pitch: 0, yaw: 0 }
        setOrientationPad({ x: 0, y: 0 })
      }
      held.add(key)
      update()
    }
    const handleUp = (event: KeyboardEvent) => {
      const key = event.key.toLowerCase()
      if (!held.has(key)) return
      held.delete(key)
      update()
    }
    const clear = () => {
      held.clear()
      keyInputRef.current = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0, j6: 0 }
    }
    window.addEventListener('keydown', handleDown)
    window.addEventListener('keyup', handleUp)
    window.addEventListener('blur', clear)
    return () => {
      window.removeEventListener('keydown', handleDown)
      window.removeEventListener('keyup', handleUp)
      window.removeEventListener('blur', clear)
      clear()
    }
  }, [controlMode])

  useEffect(() => {
    const handleVisibility = () => {
      if (document.hidden) stopMotion()
    }
    document.addEventListener('visibilitychange', handleVisibility)
    return () => document.removeEventListener('visibilitychange', handleVisibility)
  }, [])

  useEffect(() => {
    if (controlMode !== 'linear') {
      if (jogStreamActiveRef.current) {
        jogStreamActiveRef.current = false
        void api.stopArmJog()
      }
      return
    }
    const timer = window.setInterval(() => {
      if (!canMove) {
        if (jogStreamActiveRef.current) {
          jogStreamActiveRef.current = false
          void api.stopArmJog()
        }
        return
      }
      const slider = linearSliderRef.current
      const keys = keyInputRef.current
      const orientation = orientationInputRef.current
      const clamp = (value: number) => Math.max(-1, Math.min(1, value))
      const x = clamp(slider.x + keys.x)
      const y = clamp(slider.y + keys.y)
      const z = clamp(slider.z + keys.z)
      const roll = clamp(keys.roll)
      const pitch = clamp((orientationActiveRef.current ? orientation.pitch : 0) + keys.pitch)
      const yaw = clamp((orientationActiveRef.current ? orientation.yaw : 0) + keys.yaw)
      const j6 = clamp(keys.j6)
      if (!x && !y && !z && !roll && !pitch && !yaw && !j6) {
        if (jogStreamActiveRef.current) {
          jogStreamActiveRef.current = false
          void api.stopArmJog()
        }
        return
      }
      jogStreamActiveRef.current = true
      void api
        .updateArmJog(x, y, z, roll, pitch, yaw, j6, linearSpeedScale)
        .then((snapshot) => applySnapshot(snapshot))
        .catch((err) => {
          setError(err instanceof Error ? err.message : String(err))
        })
    }, 100)
    return () => {
      window.clearInterval(timer)
      if (jogStreamActiveRef.current) {
        jogStreamActiveRef.current = false
        void api.stopArmJog()
      }
    }
  }, [controlMode, canMove, linearSpeedScale])

  useEffect(() => {
    let alive = true
    api
      .listArmPoses()
      .then((list) => {
        if (alive) setPoses(list)
      })
      .catch(() => {
        // Pose list is optional; the main status error covers a dead bridge.
      })
    api
      .listArmSequences()
      .then((list) => {
        if (alive) setSequences(list)
      })
      .catch(() => {
        // Sequence list is optional too.
      })
    api
      .getHandEyeCalibration()
      .then((calibration) => {
        if (alive) setVisionCalib(calibration)
      })
      .catch(() => {
        // Vision calibration is optional until the operator starts it.
      })
    return () => {
      alive = false
      stopSequenceRef.current = true
    }
  }, [])

  function addLog(text: string, level: LogEntry['level']) {
    setLog((entries) =>
      [{ id: `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`, t: new Date().toISOString(), text, level }, ...entries].slice(0, 40),
    )
  }

  function applySnapshot(s: ArmStatus) {
    setStatus(s)
    setError(s.error)
    if (s.joints_deg) setTargets(s.joints_deg.map((v) => v.toFixed(1)))
  }

  async function run(label: string, action: () => Promise<ArmStatus>) {
    setPending(true)
    setError(null)
    try {
      const s = await action()
      applySnapshot(s)
      addLog(label, s.connected ? 'info' : 'warn')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      addLog(`${label} — ${msg}`, 'error')
      try {
        // Pull the fault/recovery fields immediately instead of waiting for the poll.
        applySnapshot(await api.getArmStatus())
      } catch {
        // The poll will retry if the bridge is temporarily unreachable.
      }
    } finally {
      setPending(false)
    }
  }

  const hasGripper = !!status?.tool?.includes('GRIPPER')
  const canUseGripper =
    !!status?.connected && !status.learning_mode && !status.busy && !status.fault && !pending && hasGripper

  useEffect(() => {
    const handleGripperKey = (event: KeyboardEvent) => {
      if (event.repeat || event.metaKey || event.ctrlKey || event.altKey) return
      const target = event.target as HTMLElement | null
      if (target && (target.tagName === 'INPUT' || target.tagName === 'SELECT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) return
      const key = event.key.toLowerCase()
      if (key !== 'g' && key !== 'h') return
      event.preventDefault()
      if (!canUseGripper) return
      if (key === 'g') {
        void run('Gripper close (G)', () => api.closeArmGripper())
      } else {
        void run('Gripper open (H)', () => api.openArmGripper())
      }
    }
    window.addEventListener('keydown', handleGripperKey)
    return () => window.removeEventListener('keydown', handleGripperKey)
  }, [canUseGripper])
  const j6Limits = status?.limits_deg?.[5] ?? [-144.46, 144.46]
  const addableStep =
    stepToAdd === ARM_SEQUENCE_GRIPPER_OPEN ||
    stepToAdd === ARM_SEQUENCE_GRIPPER_CLOSE ||
    poses.some((p) => p.name === stepToAdd)
      ? stepToAdd
      : (poses[0]?.name ?? ARM_SEQUENCE_GRIPPER_CLOSE)
  const speedOptions = Array.from(new Set([5, 10, 15, 20, status?.max_velocity ?? 5])).sort(
    (a, b) => a - b,
  )
  const reachOptions = Array.from(
    new Set([0.4, 0.42, 0.44, 0.45, 0.46, 0.47, 0.48, 0.5, status?.max_reach_m ?? 0.47]),
  ).sort((a, b) => a - b)

  function connect() {
    const ip = host.trim()
    if (!ip) return
    localStorage.setItem('packtug.armHost', ip)
    void run(`Connect ${ip}`, () => api.connectArm(ip))
  }

  function disconnect() {
    void run('Disconnect', () => api.disconnectArm())
  }

  function jog(index: number, direction: number) {
    const delta = direction * step
    void run(`${JOINTS[index].id} ${delta > 0 ? '+' : ''}${delta}°`, () => api.jogArmJoint(index + 1, delta))
  }

  function startLinearAxis() {
    // A position-only gesture must not inherit a stale sphere orientation.
    orientationActiveRef.current = false
    orientationInputRef.current = { pitch: 0, yaw: 0 }
    setOrientationPad({ x: 0, y: 0 })
  }

  function setLinearAxis(key: 'x' | 'y' | 'z', value: number) {
    linearSliderRef.current = { ...linearSliderRef.current, [key]: value }
    setLinearSliders((current) => ({ ...current, [key]: value }))
  }

  function stopLinearAxis(key: 'x' | 'y' | 'z') {
    setLinearAxis(key, 0)
  }

  function stopMotion() {
    linearSliderRef.current = { x: 0, y: 0, z: 0 }
    setLinearSliders({ x: 0, y: 0, z: 0 })
    keyInputRef.current = { x: 0, y: 0, z: 0, roll: 0, pitch: 0, yaw: 0, j6: 0 }
    orientationActiveRef.current = false
    orientationInputRef.current = { pitch: 0, yaw: 0 }
    setOrientationPad({ x: 0, y: 0 })
    jogStreamActiveRef.current = false
    void api.stopArmMotion()
  }

  function updateOrientationPad(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect()
    let x = (event.clientX - rect.left - rect.width / 2) / (rect.width / 2)
    let y = (event.clientY - rect.top - rect.height / 2) / (rect.height / 2)
    const magnitude = Math.hypot(x, y)
    if (magnitude > 1) {
      x /= magnitude
      y /= magnitude
    }
    setOrientationPad({ x, y })
    orientationInputRef.current = { yaw: x, pitch: -y }
  }

  function startOrientationPad(event: PointerEvent<HTMLDivElement>) {
    orientationDraggingRef.current = true
    orientationActiveRef.current = true
    event.currentTarget.setPointerCapture(event.pointerId)
    updateOrientationPad(event)
  }

  function moveOrientationPad(event: PointerEvent<HTMLDivElement>) {
    if (orientationDraggingRef.current) updateOrientationPad(event)
  }

  function stopOrientationPad() {
    orientationDraggingRef.current = false
    orientationActiveRef.current = false
    setOrientationPad({ x: 0, y: 0 })
    orientationInputRef.current = { pitch: 0, yaw: 0 }
  }

  function updateJ6(value: number) {
    j6DraggingRef.current = true
    setJ6Target(value)
  }

  function commitJ6(value: number) {
    j6DraggingRef.current = false
    if (!Number.isFinite(value)) return
    void run(`J6 wrist roll ${value.toFixed(1)}°`, () => api.moveArmJoint(6, value))
  }

  function syncTargets() {
    if (!status?.joints_deg) return
    setTargets(status.joints_deg.map((v) => v.toFixed(1)))
    addLog('Targets synced to current joints', 'info')
  }

  function applyTargets() {
    const nums = targets.map(Number)
    if (nums.some((v) => !Number.isFinite(v))) {
      setError('All six targets must be numbers.')
      return
    }
    void run('Move joints', () => api.moveArmJoints(nums))
  }

  function enableMotors() {
    void run('Enable motors', () => api.enableArmMotors())
  }

  async function calibrate() {
    if (!confirmCalibration) return
    await run('Initialize / auto-calibrate', () => api.calibrateArm())
    setConfirmCalibration(false)
  }

  async function releaseMotors() {
    if (!confirmRelease) return
    await run('Manual pose (motors released)', () => api.setArmLearning(true))
    setConfirmRelease(false)
  }

  function commitJoint(index: number, value: number) {
    if (!canMove || !Number.isFinite(value)) return
    const current = status?.joints_deg?.[index]
    if (current !== undefined && Math.abs(value - current) < 0.05) return
    void run(`${JOINTS[index].id} → ${value.toFixed(1)}°`, () => api.moveArmJoint(index + 1, value))
  }

  function openGripper() {
    void run('Gripper open', () => api.openArmGripper())
  }

  function closeGripper() {
    void run('Gripper close', () => api.closeArmGripper())
  }

  function scanTool() {
    void run('Scan tool', () => api.scanArmTool())
  }

  function rawGripper(open: boolean) {
    void run(`Raw gripper ${open ? 'open' : 'close'}`, () => api.rawArmGripper(open))
  }

  function homeArm() {
    void run('Home / fold', () => api.homeArm())
  }

  function recoverArm() {
    void run('Recover to last safe point', () => api.recoverArm())
  }

  function clearFault() {
    if (!window.confirm('Only clear the fault after you have inspected the arm and confirmed the workspace is clear.')) {
      return
    }
    void run('Clear motion fault', () => api.clearArmFault())
  }

  async function switchCameraMode(mode: 'rgb' | 'depth') {
    if (mode === camMode && !camError) {
      setCamKey((value) => value + 1)
      return
    }
    setCamSwitching(true)
    setCamTargetMode(mode)
    setCamError(false)
    setFrozenVisionFrame(null)
    setQr(null)
    try {
      const res = await fetch(`/api/camera/mode?value=${mode}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = (await res.json()) as { ok: boolean; error?: string }
      if (!data.ok) throw new Error(data.error ?? 'camera mode switch failed')
      setCamMode(mode)
      setCamKey((value) => value + 1)
    } catch (err) {
      setCamError(true)
      addLog(`Camera ${mode} — ${err instanceof Error ? err.message : String(err)}`, 'error')
    } finally {
      setCamSwitching(false)
      setCamTargetMode(null)
    }
  }

  function setSpeed(percent: number) {
    void run(`Speed ${percent}%`, () => api.setArmVelocity(percent))
  }

  function setReach(maxReachM: number) {
    void run(`Max reach ${maxReachM.toFixed(2)} m`, () => api.setArmReach(maxReachM))
  }

  function imagePoint(event: PointerEvent<HTMLImageElement>) {
    const image = cameraImgRef.current ?? event.currentTarget
    const rect = image.getBoundingClientRect()
    const width = image.naturalWidth || 640
    const height = image.naturalHeight || 480
    const scale = Math.min(rect.width / width, rect.height / height)
    const renderedWidth = width * scale
    const renderedHeight = height * scale
    const left = rect.left + (rect.width - renderedWidth) / 2
    const top = rect.top + (rect.height - renderedHeight) / 2
    return {
      x: Math.max(0, Math.min(width, (event.clientX - left) / scale)),
      y: Math.max(0, Math.min(height, (event.clientY - top) / scale)),
    }
  }

  function captureVisibleCameraFrame(): { dataUrl: string; capturedAt: number } {
    const image = cameraImgRef.current
    if (!image || !image.naturalWidth || !image.naturalHeight) {
      throw new Error('Camera frame is not ready yet.')
    }
    const canvas = document.createElement('canvas')
    canvas.width = image.naturalWidth
    canvas.height = image.naturalHeight
    const context = canvas.getContext('2d')
    if (!context) throw new Error('Browser could not capture the camera frame.')
    context.drawImage(image, 0, 0, canvas.width, canvas.height)
    return { dataUrl: canvas.toDataURL('image/jpeg', 0.92), capturedAt: Date.now() / 1000 }
  }

  function normalisedRect(a: { x: number; y: number }, b: { x: number; y: number }) {
    return {
      x: Math.min(a.x, b.x),
      y: Math.min(a.y, b.y),
      w: Math.abs(a.x - b.x),
      h: Math.abs(a.y - b.y),
    }
  }

  async function runQrDetection(
    x: number,
    y: number,
    roi?: { x: number; y: number; w: number; h: number },
    capturedFrame?: { dataUrl: string; capturedAt: number },
  ) {
    setQrPending(true)
    setQrError(null)
    setQr(null)
    try {
      const frame = capturedFrame ?? captureVisibleCameraFrame()
      setFrozenVisionFrame(frame.dataUrl)
      const liveStatus = await api.getArmStatus().catch(() => null)
      const result = await api.detectQr(x, y, 60, roi, {
        imageDataUrl: frame.dataUrl,
        armPose: liveStatus?.pose ?? null,
        capturedAt: frame.capturedAt,
        targetOffset: qrTargetOffset,
      })
      setQr(result)
      if (result.ok) {
        addLog(
          `QR ${result.center?.x.toFixed(1)},${result.center?.y.toFixed(1)} px · ${result.angle_deg?.toFixed(1)}°`,
          'info',
        )
      } else {
        setQrError(result.error ?? 'QR not found near selection')
      }
    } catch (err) {
      setQrError(err instanceof Error ? err.message : String(err))
    } finally {
      setQrPending(false)
    }
  }

  function startQrDrag(event: PointerEvent<HTMLImageElement>) {
    if (camMode !== 'rgb' || camSwitching) return
    event.preventDefault()
    const point = imagePoint(event)
    try {
      dragFrameRef.current = captureVisibleCameraFrame()
      setFrozenVisionFrame(dragFrameRef.current.dataUrl)
    } catch (err) {
      setQrError(err instanceof Error ? err.message : String(err))
      return
    }
    dragStartRef.current = point
    setDragRect({ x: point.x, y: point.y, w: 0, h: 0 })
    setQr(null)
    setQrError(null)
    try {
      event.currentTarget.setPointerCapture(event.pointerId)
    } catch {
      // Pointer capture is optional; the drag still works while inside the image.
    }
  }

  function moveQrDrag(event: PointerEvent<HTMLImageElement>) {
    if (!dragStartRef.current) return
    const point = imagePoint(event)
    setDragRect(normalisedRect(dragStartRef.current, point))
  }

  function finishQrDrag(event: PointerEvent<HTMLImageElement>) {
    const start = dragStartRef.current
    if (!start) return
    const point = imagePoint(event)
    const rect = normalisedRect(start, point)
    dragStartRef.current = null
    try {
      event.currentTarget.releasePointerCapture(event.pointerId)
    } catch {
      // Already released.
    }
    const capturedFrame = dragFrameRef.current
    dragFrameRef.current = null
    if (!capturedFrame) return
    setFrozenVisionFrame(capturedFrame.dataUrl)
    if (rect.w >= 20 && rect.h >= 20) {
      setDragRect(rect)
      void runQrDetection(rect.x + rect.w / 2, rect.y + rect.h / 2, rect, capturedFrame)
    } else {
      setDragRect(null)
      void runQrDetection(point.x, point.y, undefined, capturedFrame)
    }
  }

  function cancelQrDrag() {
    dragStartRef.current = null
    dragFrameRef.current = null
    setDragRect(null)
  }

  async function captureVisionPoint() {
    if (frozenVisionFrame) {
      setVisionMessage('Resume the live view before capturing the next board pose.')
      return
    }
    setVisionPending(true)
    setVisionMessage(null)
    try {
      const frame = captureVisibleCameraFrame()
      setFrozenVisionFrame(frame.dataUrl)
      const liveStatus = await api.getArmStatus()
      if (!liveStatus.pose) throw new Error('Arm pose is unavailable.')
      const calibration = await api.captureHandEyeCalibrationPoint({
        imageDataUrl: frame.dataUrl,
        armPose: liveStatus.pose,
        capturedAt: frame.capturedAt,
      })
      setVisionCalib(calibration)
      setVisionMessage(`Captured ${calibration.samples.length}/${calibration.needed} wrist poses.`)
      addLog(
        `Hand-eye sample ${calibration.samples.length}: ${calibration.last_capture?.corners ?? 0} ChArUco corners · ${calibration.last_capture?.reprojection_error_px.toFixed(2) ?? '—'} px`,
        'info',
      )
    } catch (err) {
      setVisionMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setVisionPending(false)
    }
  }

  async function solveVisionCalibration() {
    setVisionPending(true)
    setVisionMessage(null)
    try {
      const calibration = await api.solveHandEyeCalibration()
      setVisionCalib(calibration)
      setVisionMessage('Wrist-camera transform solved.')
      addLog('Hand-eye calibration solved', 'success')
    } catch (err) {
      setVisionMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setVisionPending(false)
    }
  }

  async function resetVisionCalibration() {
    if (!window.confirm('Reset all wrist-camera calibration samples?')) return
    setVisionPending(true)
    setVisionMessage(null)
    try {
      const calibration = await api.resetHandEyeCalibration()
      setVisionCalib(calibration)
      setVisionMessage('Vision calibration cleared.')
    } catch (err) {
      setVisionMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setVisionPending(false)
    }
  }

  async function fillTargetFromQr() {
    if (!qr?.ok || !qr.target_pose) {
      setPathError(qr?.pose_error ?? 'Solve wrist-camera calibration before using the QR target.')
      return
    }
    setPathTarget((current) => ({
      ...current,
      x: qr.target_pose!.x,
      y: qr.target_pose!.y,
      z: qr.target_pose!.z,
    }))
    addLog(
      `Target XYZ ← QR offset ${qr.target_pose.x.toFixed(3)}, ${qr.target_pose.y.toFixed(3)}, ${qr.target_pose.z.toFixed(3)}`,
      'info',
    )
  }

  function setTargetToCageCentre() {
    const yaw = (pathCage.yaw * Math.PI) / 180
    const localY = pathCage.depth / 2
    const zLocal = Math.max(
      pathCage.clearance_m + 0.02,
      Math.min(0.1, pathCage.height / 2),
    )
    const x = Number((pathCage.x - localY * Math.sin(yaw)).toFixed(3))
    const y = Number((pathCage.y + localY * Math.cos(yaw)).toFixed(3))
    const z = Number((pathCage.z + zLocal).toFixed(3))
    setPathTarget((current) => ({
      ...current,
      x,
      y,
      z,
      ...(status?.pose
        ? {
            roll: status.pose.roll,
            pitch: status.pose.pitch,
            yaw: status.pose.yaw,
          }
        : {}),
    }))
    addLog(
      `Target = cage floor centre (${x.toFixed(3)}, ${y.toFixed(3)}, ${z.toFixed(3)} m)`,
      'info',
    )
  }

  async function planPickPath() {
    setPathPending(true)
    setPathError(null)
    try {
      const points = cageCorners.map((point, index) =>
        point ?? (cageIgnored[index] ? { x: 0, y: 0, z: 0 } : null),
      )
      const anyCorner = cageCorners.some((point) => point !== null)
      const corners = points.some((point) => point === null)
        ? null
        : (points as PickPathVec3[])
      if (anyCorner && corners === null) {
        setPathError('Record all reachable cage corners, or clear cage teaching.')
        return
      }
      const ignored = cageIgnored.flatMap((value, index) => (value ? [index] : []))
      if (useHome && !homeRecorded) {
        setPathError(
          'Record the home pose first: click Home / fold, then Home = current.',
        )
        return
      }
      const result = await api.planArmPickPath({
        start: useCurrentStart ? null : pathStart,
        ...(useVia ? { via: pathVia } : {}),
        ...(useHome ? { home: pathHome } : {}),
        target: pathTarget,
        approach_m: pathApproach,
        gate_clearance_m: pathGateClearance,
        payload_radius_m: pathPayloadRadius,
        cage: pathCage,
        ...(corners ? { corners, ignored_corners: ignored } : {}),
      })
      setPathResult(result)
      const startPoint = result.points.find((point) => point.kind === 'start')
      if (startPoint) setLastStart({ ...startPoint.pose })
      addLog(
        `Pick path ${result.ok ? 'clear' : 'blocked'} · ${result.points.length} points`,
        result.ok ? 'success' : 'warn',
      )
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setPathError(message)
      setPathResult(null)
      addLog(`Pick path — ${message}`, 'error')
    } finally {
      setPathPending(false)
    }
  }

  function cornerRequestData(): {
    corners: PickPathVec3[] | null
    ignored: number[]
  } {
    const points = cageCorners.map((point, index) =>
      point ?? (cageIgnored[index] ? { x: 0, y: 0, z: 0 } : null),
    )
    return {
      corners: points.some((point) => point === null)
        ? null
        : (points as PickPathVec3[]),
      ignored: cageIgnored.flatMap((value, index) => (value ? [index] : [])),
    }
  }

  async function executePickPlace() {
    if (!status?.connected) {
      setExecError('Connect the arm first.')
      return
    }
    if (execPending) return
    if (!homeRecorded) {
      setExecError('Record home first: Home / fold, then Home = current.')
      return
    }
    const confirmed = window.confirm(
      'This will:\n' +
        (useCurrentStart ? '' : '1. move to the entered Start pose,\n') +
        '2. level the gripper (roll/pitch) at the grasp pose,\n' +
        '3. close the gripper and keep that level attitude while pulling back,\n' +
        '4. reorient at the outer retract point (not near the base),\n' +
        '5. use the retract point as a safe transit and follow the checked path to the target,\n' +
        '6. verify the level/cage attitude before opening.\n\n' +
        'Keep the workspace clear and a hand on the power switch. Continue?',
    )
    if (!confirmed) return
    setExecPending(true)
    setExecError(null)
    setExecResult(null)
    try {
      const { corners, ignored } = cornerRequestData()
      if (cageCorners.some((point) => point !== null) && corners === null) {
        setExecError('Record all reachable cage corners first.')
        return
      }
      const result = await api.executeArmPick({
        ...(useCurrentStart ? {} : { start: pathStart }),
        target: pathTarget,
        home: pathHome,
        home_lift_m: execHomeLift,
        retract_m: execRetract,
        approach_m: pathApproach,
        gate_clearance_m: pathGateClearance,
        payload_radius_m: pathPayloadRadius,
        cage: pathCage,
        ...(corners ? { corners, ignored_corners: ignored } : {}),
        speed_percent: status?.max_velocity ?? 5,
        return_home_after: returnHomeAfter,
      })
      setExecResult(result)
      if (!result.ok) setExecError(result.error ?? 'Execution stopped')
      addLog(
        `Execute ${result.ok ? 'done' : 'stopped'} · ${result.stage}`,
        result.ok ? 'success' : 'error',
      )
      try {
        applySnapshot(await api.getArmStatus())
      } catch {
        // Keep the local snapshot if the read fails.
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err)
      setExecError(message)
      addLog(`Execute — ${message}`, 'error')
    } finally {
      setExecPending(false)
    }
  }

  function recordCageCenter() {
    if (!status?.pose) return
    const pose = status.pose
    setPathCage((current) => ({ ...current, x: pose.x, y: pose.y, z: pose.z }))
    setCageCenterRecorded(true)
    setCageFront(null)
    setCageMessage(
      `Opening center recorded at ${pose.x.toFixed(3)}, ${pose.y.toFixed(3)}, ${pose.z.toFixed(3)} m`,
    )
    addLog('Cage opening center recorded from the current pose', 'info')
  }

  function recordCageFront() {
    if (!status?.pose) return
    const front = { ...status.pose }
    setCageFront(front)
    const dx = front.x - pathCage.x
    const dy = front.y - pathCage.y
    const distance = Math.hypot(dx, dy)
    if (distance < 0.01) {
      setCageMessage(
        'Move at least 20 mm back from the opening center before recording the front point.',
      )
      return
    }
    const yawDeg = (Math.atan2(dx / distance, -(dy / distance)) * 180) / Math.PI
    setPathCage((current) => ({ ...current, yaw: Number(yawDeg.toFixed(1)) }))
    setPathGateClearance(Math.min(0.30, Math.max(0.02, Number(distance.toFixed(3)))))
    const zNote =
      Math.abs(front.z - pathCage.z) > 0.02
        ? ` · Z differs by ${Math.round((front.z - pathCage.z) * 1000)} mm`
        : ''
    setCageMessage(
      `Front point recorded ${Math.round(distance * 1000)} mm out · yaw ${yawDeg.toFixed(1)}°${zNote}`,
    )
    addLog(`Cage yaw ${yawDeg.toFixed(1)}° from two taught points`, 'info')
  }

  function recordCageCorner(index: number) {
    if (!status?.pose) return
    const pose = status.pose
    setCageCorners((current) =>
      current.map((point, i) =>
        i === index ? { x: pose.x, y: pose.y, z: pose.z } : point,
      ),
    )
    setCageFit(null)
    setCageMessage(`Corner ${index + 1} (${CORNER_LABELS[index]}) recorded`)
    addLog(`Cage corner ${index + 1} recorded`, 'info')
  }

  function clearCageCorners() {
    setCageCorners(Array(8).fill(null))
    setCageFit(null)
    setCageMessage(null)
    setCageIgnored([...DEFAULT_IGNORED])
  }

  function toggleCageIgnored(index: number) {
    setCageIgnored((current) =>
      current.map((value, i) => (i === index ? !value : value)),
    )
    setCageFit(null)
    setCageMessage(null)
  }

  async function fitCageCorners() {
    const points = cageCorners.map((point, index) =>
      point ?? (cageIgnored[index] ? { x: 0, y: 0, z: 0 } : null),
    )
    if (points.some((point) => point === null)) {
      setCageMessage('Record all reachable corners first.')
      return
    }
    const usable = points as PickPathVec3[]
    const ignored = cageIgnored.flatMap((value, index) => (value ? [index] : []))
    setCageFitPending(true)
    try {
      const result = await api.fitArmCage(usable, {
        opening_margin: pathCage.opening_margin,
        clearance_m: pathCage.clearance_m,
        front_top_open: pathCage.front_top_open ?? true,
        ignored_corners: ignored,
      })
      setCageFit(result)
      setPathCage((current) => ({ ...current, ...result.cage }))
      const swapped =
        !!result.fit.reordered_corners?.includes(2) &&
        !!result.fit.reordered_corners?.includes(3)
      if (swapped) {
        setCageCorners((current) => {
          const next = [...current]
          ;[next[2], next[3]] = [next[3], next[2]]
          return next
        })
      }
      const notes: string[] = []
      if (ignored.length) {
        notes.push(`derived ${ignored.map((index) => index + 1).join(', ')}`)
      }
      if (swapped) notes.push('swapped BR/BL bottom')
      setCageMessage(
        `Box fitted ${Math.round(result.fit.width_m * 1000)} × ${Math.round(
          result.fit.depth_m * 1000,
        )} × ${Math.round(result.fit.height_m * 1000)} mm${
          notes.length ? ` · ${notes.join(' · ')}` : ''
        }`,
      )
      addLog(
        `Cage box fitted from 8 corners · yaw ${result.fit.yaw_deg.toFixed(1)}°`,
        'success',
      )
    } catch (err) {
      setCageMessage(err instanceof Error ? err.message : String(err))
    } finally {
      setCageFitPending(false)
    }
  }

  async function savePose() {
    const name = poseName.trim()
    if (!name) {
      setError('Pose name is required.')
      return
    }
    setPending(true)
    setError(null)
    try {
      const pose = await api.saveArmPose(name)
      setPoses(await api.listArmPoses())
      setPoseName('')
      addLog(`Pose saved: ${pose.name}`, 'info')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      addLog(`Save pose — ${msg}`, 'error')
    } finally {
      setPending(false)
    }
  }

  async function deletePose(name: string) {
    setPending(true)
    setError(null)
    try {
      await api.deleteArmPose(name)
      setPoses(await api.listArmPoses())
      setDraftSteps((steps) => steps.filter((step) => step !== name))
      addLog(`Pose deleted: ${name}`, 'warn')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      addLog(`Delete pose — ${msg}`, 'error')
    } finally {
      setPending(false)
    }
  }

  function applyPose(name: string) {
    void run(`Recall pose: ${name} (${linearPath ? 'linear' : 'joint'})`, () =>
      api.applyArmPose(name, linearPath),
    )
  }

  function addDraftStep(step = addableStep) {
    if (!step) return
    setDraftSteps((steps) => [...steps, step])
  }

  function removeDraftStep(index: number) {
    setDraftSteps((steps) => steps.filter((_, i) => i !== index))
  }

  function moveDraftStep(index: number, direction: -1 | 1) {
    setDraftSteps((steps) => {
      const next = [...steps]
      const target = index + direction
      if (target < 0 || target >= next.length) return steps
      ;[next[index], next[target]] = [next[target], next[index]]
      return next
    })
  }

  async function saveSequence() {
    const name = sequenceName.trim()
    if (!name) {
      setError('Sequence name is required.')
      return
    }
    if (!draftSteps.length) {
      setError('Add at least one step.')
      return
    }
    setPending(true)
    setError(null)
    try {
      const sequence = await api.saveArmSequence(name, draftSteps, dwellSec, transitPose || null)
      setSequences(await api.listArmSequences())
      addLog(`Sequence saved: ${sequence.name} (${sequence.poses.length} steps)`, 'info')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      addLog(`Save sequence — ${msg}`, 'error')
    } finally {
      setPending(false)
    }
  }

  async function deleteSequence(name: string) {
    setPending(true)
    setError(null)
    try {
      await api.deleteArmSequence(name)
      setSequences(await api.listArmSequences())
      addLog(`Sequence deleted: ${name}`, 'warn')
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      addLog(`Delete sequence — ${msg}`, 'error')
    } finally {
      setPending(false)
    }
  }

  function loadSequence(sequence: ArmSequence) {
    setSequenceName(sequence.name)
    setDraftSteps([...sequence.poses])
    setDwellSec(sequence.dwell_sec)
    setTransitPose(sequence.transit ?? '')
    addLog(`Sequence loaded: ${sequence.name}`, 'info')
  }

  async function waitWithStop(ms: number) {
    const end = Date.now() + ms
    while (Date.now() < end) {
      if (stopSequenceRef.current) return
      await sleep(Math.min(100, Math.max(0, end - Date.now())))
    }
  }

  async function runSequence(sequence: ArmSequence) {
    if (pending || sequenceRun) return
    if (!canMove) {
      setError('Connect the arm and clear calibration / learning faults before running a sequence.')
      return
    }
    const needsGripper = sequence.poses.some(isGripperStep)
    if (needsGripper && !canUseGripper) {
      setError('This sequence contains gripper steps; connect a working gripper first.')
      return
    }
    stopSequenceRef.current = false
    setSequenceStopping(false)
    setPending(true)
    setError(null)
    setSequenceRun({ name: sequence.name, step: 0, total: sequence.poses.length, phase: 'pose' })
    addLog(`Sequence start: ${sequence.name} (${sequence.poses.length} steps)`, 'info')
    let stopped = false
    try {
      for (let i = 0; i < sequence.poses.length; i++) {
        if (stopSequenceRef.current) {
          stopped = true
          break
        }
        const kind = sequence.poses[i]
        const gripperStep = isGripperStep(kind)
        if (!gripperStep && sequence.transit && sequence.transit !== kind) {
          setSequenceRun({ name: sequence.name, step: i + 1, total: sequence.poses.length, phase: 'transit' })
          addLog(`Transit → ${sequence.transit === '__home__' ? 'Home / fold' : sequence.transit}`, 'info')
          const transitSnapshot =
            sequence.transit === '__home__'
              ? await api.homeArm()
              : await api.applyArmPose(sequence.transit, linearPath)
          applySnapshot(transitSnapshot)
          if (stopSequenceRef.current) {
            stopped = true
            break
          }
        }
        setSequenceRun({
          name: sequence.name,
          step: i + 1,
          total: sequence.poses.length,
          phase: gripperStep ? 'gripper' : 'pose',
        })
        addLog(`Step ${i + 1}/${sequence.poses.length}: ${sequenceStepLabel(kind)}`, 'info')
        const snapshot = gripperStep
          ? kind === ARM_SEQUENCE_GRIPPER_OPEN
            ? await api.openArmGripper()
            : await api.closeArmGripper()
          : await api.applyArmPose(kind, linearPath)
        applySnapshot(snapshot)
        if (stopSequenceRef.current) {
          stopped = true
          break
        }
        if (i < sequence.poses.length - 1 && sequence.dwell_sec > 0) {
          await waitWithStop(sequence.dwell_sec * 1000)
        }
      }
      if (stopped || stopSequenceRef.current) {
        addLog(`Sequence stopped: ${sequence.name}`, 'warn')
      } else {
        addLog(`Sequence complete: ${sequence.name}`, 'success')
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      setError(msg)
      addLog(`Sequence aborted — ${msg}`, 'error')
    } finally {
      stopSequenceRef.current = false
      setSequenceStopping(false)
      setSequenceRun(null)
      setPending(false)
    }
  }

  function stopSequence() {
    if (!sequenceRun) return
    stopSequenceRef.current = true
    setSequenceStopping(true)
    addLog(`Stop requested: ${sequenceRun.name} (after the current step)`, 'warn')
  }

  const connectionText = !status?.connected
    ? 'disconnected'
    : status.calibration_needed
      ? 'calibration required'
      : status.learning_mode
        ? 'motors released'
        : 'connected'

  return (
    <>
      <PageHeader
        title="Arm Control"
        desc="Niryo Ned1 joint terminal over PyNiryo. Speed 1–20% (default 20%), ≤60° per command."
        actions={
          <>
            <button className="btn danger" onClick={stopMotion} disabled={!status?.connected}>
              STOP ARM
            </button>
            {status?.mock && <Badge tone="violet">MOCK ARM</Badge>}
            {status?.fault && <Badge tone="red" dot>FAULT</Badge>}
            {status?.hardware_motion_fault && <Badge tone="red" dot>HARDWARE</Badge>}
            {!status?.hardware_motion_fault && status?.hardware_tool_fault && (
              <Badge tone="amber" dot>TOOL</Badge>
            )}
            <Badge
              tone={!status?.connected ? 'grey' : status.calibration_needed || status.learning_mode ? 'amber' : 'green'}
              dot
            >
              {connectionText}
            </Badge>
          </>
        }
      />

      <div className="stale-warning">
        No hardware E-stop on this arm. Keep the workspace clear and a hand on the power switch. A slider release may
        move a joint up to 60°, and manual posing releases the motors — hold the arm before enabling free motion.
      </div>

      {status?.hardware_motion_fault && (
        <div className="error-banner">
          Hardware fault: {status.hardware_motion_fault}. Clear the obstruction/load, power-cycle the arm, then
          reconnect before moving.
        </div>
      )}

      {!status?.hardware_motion_fault && status?.hardware_tool_fault && (
        <div className="stale-warning">
          Tool warning: {status.hardware_tool_fault}. Arm motion remains available; check the gripper power and
          connection.
        </div>
      )}

      <Card
        title="Camera"
        hint="Intel RealSense D455 · live RGB / depth stream from the Jetson"
        right={
          <>
            <div className="row" style={{ gap: 4 }}>
              {(['rgb', 'depth'] as const).map((mode) => (
                <button
                  key={mode}
                  className={`btn sm ${camMode === mode ? 'primary' : ''}`}
                  onClick={() => void switchCameraMode(mode)}
                  disabled={camSwitching}
                >
                  {mode === 'rgb' ? 'RGB' : 'Depth'}
                </button>
              ))}
              <button
                className="btn sm"
                onClick={() => {
                  setCamError(false)
                  setCamKey((value) => value + 1)
                }}
                disabled={camSwitching}
              >
                Reconnect
              </button>
            </div>
            <Badge tone={camError ? 'red' : camSwitching ? 'amber' : 'green'} dot>
              {camError ? 'offline' : camSwitching ? 'switching' : 'live'}
            </Badge>
          </>
        }
      >
        {camSwitching ? (
          <div className="camera-error">
            <p>Switching camera to {(camTargetMode ?? camMode) === 'rgb' ? 'RGB' : 'depth'}…</p>
            <p className="faint">The Jetson restarts the RealSense sensor in the selected mode (~5–10 s).</p>
          </div>
        ) : camError ? (
          <div className="camera-error">
            <p>Camera stream unavailable.</p>
            <p className="faint">Check the Jetson camera node, `CAMERA_STREAM_URL`, and the Tailscale link.</p>
            <button
              className="btn sm"
              onClick={() => {
                setCamError(false)
                setCamKey((value) => value + 1)
              }}
            >
              Retry stream
            </button>
          </div>
        ) : (
          <>
            <div className="camera-stage">
              <img
                key={`${camMode}-${camKey}`}
                ref={cameraImgRef}
                className="camera-feed"
                src={
                  frozenVisionFrame && camMode === 'rgb'
                    ? frozenVisionFrame
                    : `${camMode === 'rgb' ? '/api/camera/stream' : '/api/camera/depth'}?t=${camKey}`
                }
                alt={camMode === 'rgb' ? 'RealSense RGB camera' : 'RealSense colorized depth'}
                title={
                  camMode === 'rgb'
                    ? 'Click the QR tag, or drag a box around it when it is small'
                    : undefined
                }
                style={{ cursor: camMode === 'rgb' ? 'crosshair' : 'default' }}
                draggable={false}
                onPointerDown={startQrDrag}
                onPointerMove={moveQrDrag}
                onPointerUp={finishQrDrag}
                onPointerCancel={cancelQrDrag}
                onError={() => setCamError(true)}
              />
              {camMode === 'rgb' &&
                (dragRect || (qr?.ok && qr.frame && qr.center && qr.corners)) && (
                  <svg
                    className="camera-overlay"
                    viewBox={`0 0 ${qr?.frame?.w ?? 640} ${qr?.frame?.h ?? 480}`}
                    preserveAspectRatio="xMidYMid meet"
                    aria-hidden="true"
                  >
                    {dragRect && (
                      <rect
                        x={dragRect.x}
                        y={dragRect.y}
                        width={dragRect.w}
                        height={dragRect.h}
                        fill="rgba(251, 191, 36, 0.08)"
                        stroke="var(--amber)"
                        strokeWidth="1.5"
                        strokeDasharray="6 4"
                        vectorEffect="non-scaling-stroke"
                      />
                    )}
                    {qr?.ok && qr.corners && qr.center && (
                      <>
                        <polygon
                          points={qr.corners.map((point) => `${point.x},${point.y}`).join(' ')}
                          fill="rgba(34, 211, 238, 0.14)"
                          stroke="var(--accent)"
                          strokeWidth="2"
                          vectorEffect="non-scaling-stroke"
                        />
                        <circle
                          cx={qr.center.x}
                          cy={qr.center.y}
                          r="5"
                          fill="var(--accent)"
                          stroke="#0a0e14"
                          strokeWidth="1"
                        />
                      </>
                    )}
                  </svg>
                )}
            </div>
            {camMode === 'rgb' && (
              <div className="camera-stats row between">
                <span>
                {qrPending ? (
                  'Detecting QR in selection…'
                ) : qrError ? (
                  <span style={{ color: 'var(--amber)' }}>{qrError}</span>
                ) : qr?.ok && qr.center ? (
                  <span className="mono">
                    QR center {qr.center.x.toFixed(1)}, {qr.center.y.toFixed(1)} px · angle{' '}
                    {qr.angle_deg?.toFixed(1) ?? '—'}° · area {qr.area_px ?? '—'} px²
                    {qr.camera_pose
                      ? ` · depth ${(qr.camera_pose.tvec[2] * 1000).toFixed(0)} mm · ${qr.camera_pose.reprojection_error_px.toFixed(2)} px`
                      : ''}
                    {qr.pose_error ? ` · ${qr.pose_error}` : ''}
                  </span>
                ) : (
                  'Click the QR tag, or drag a box around it if it is too small to click accurately.'
                )}
                </span>
                {frozenVisionFrame && (
                  <button
                    className="btn sm"
                    onClick={() => {
                      setFrozenVisionFrame(null)
                      setDragRect(null)
                      setQr(null)
                    }}
                  >
                    Resume live
                  </button>
                )}
              </div>
            )}
            {camMode === 'depth' && (
              <div className="camera-stats mono">
                nearest valid depth: {depthStats?.nearest_m != null ? `${depthStats.nearest_m.toFixed(3)} m` : '—'}
                {' · '}farthest: {depthStats?.farthest_m != null ? `${depthStats.farthest_m.toFixed(3)} m` : '—'}
                {' · '}valid pixels: {depthStats?.valid_pixels ?? '—'}
              </div>
            )}
          </>
        )}
      </Card>

      <Card title="Wrist-camera calibration" hint="Eye-in-hand · fixed 5×7 ChArUco board · 10–15 stationary poses">
        <div className="stack">
          <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>
            Keep the printed ChArUco board fixed. Move the arm to varied positions and orientations, stop completely,
            then capture the board and paired arm pose. Resume live before each new pose; do not move the board.
          </p>
          <div className="row between">
            <span className="muted">Points</span>
            <span className="mono">
              {visionCalib?.samples.length ?? 0}/{visionCalib?.needed ?? 10}
            </span>
          </div>
          <div className="row between">
            <span className="muted">Camera → tool transform</span>
            <span className="mono">
              {visionCalib?.camera_to_tool ? 'solved' : '—'}
            </span>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <NumberField
              label="QR→target X (m)"
              value={qrTargetOffset.x}
              step={0.005}
              onChange={(x) => setQrTargetOffset((current) => ({ ...current, x }))}
            />
            <NumberField
              label="QR→target Y (m)"
              value={qrTargetOffset.y}
              step={0.005}
              onChange={(y) => setQrTargetOffset((current) => ({ ...current, y }))}
            />
            <NumberField
              label="QR→target Z (m)"
              value={qrTargetOffset.z}
              step={0.005}
              onChange={(z) => setQrTargetOffset((current) => ({ ...current, z }))}
            />
          </div>
          <span className="faint" style={{ fontSize: 11.5 }}>
            Offset uses QR-local axes: X right, Y toward the printed top, Z out of the label.
            Default Y 0.0525 m is the current upright paper block's QR-centre → gripper-web offset.
          </span>
          <div className="row">
            <button
              className="btn primary"
              onClick={() => void captureVisionPoint()}
              disabled={!status?.connected || !!frozenVisionFrame || visionPending}
            >
              {visionPending ? 'Working…' : 'Capture board pose'}
            </button>
            <button
              className="btn"
              onClick={() => void solveVisionCalibration()}
              disabled={(visionCalib?.samples.length ?? 0) < (visionCalib?.needed ?? 10) || visionPending}
            >
              Solve
            </button>
            <button className="btn danger" onClick={() => void resetVisionCalibration()} disabled={visionPending}>
              Reset
            </button>
          </div>
          {visionMessage && <span className="faint">{visionMessage}</span>}
        </div>
      </Card>

      <Card
        title="Pick path (dry-run)"
        hint="start → home → [via] → gate → approach → target · no motion is sent"
        right={
          <Badge tone={pathResult ? (pathResult.ok ? 'green' : 'red') : 'grey'} dot>
            {pathResult ? (pathResult.ok ? 'clear' : 'blocked') : 'not planned'}
          </Badge>
        }
      >
        <div className="stack">
          <p className="faint" style={{ margin: 0, fontSize: 12.5 }}>
            The planner closes the gripper at the start, transits through home, then routes
            through the cage opening (gate) and moves horizontally into the placement point.
            A via point is optional. Cage pose is in the arm base frame; yaw −90° means the
            opening faces the arm and depth runs away from it.
          </p>
          <div className="row" style={{ gap: 8 }}>
            <label className="row" style={{ gap: 5, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={useCurrentStart}
                onChange={(event) => setUseCurrentStart(event.target.checked)}
              />
              Start = current pose
            </label>
            {!useCurrentStart && (
              <button
                className="btn sm"
                onClick={() => status?.pose && setPathStart({ ...status.pose })}
                disabled={!status?.pose}
              >
                Fill start from current
              </button>
            )}
            {lastStart && (
              <button
                className="btn sm"
                onClick={() => {
                  setPathStart({ ...lastStart })
                  setUseCurrentStart(false)
                }}
              >
                Use last start
              </button>
            )}
            {lastStart && (
              <span className="faint" style={{ fontSize: 11 }}>
                last: {(lastStart.x * 1000).toFixed(0)},{' '}
                {(lastStart.y * 1000).toFixed(0)}, {(lastStart.z * 1000).toFixed(0)} mm
              </span>
            )}
          </div>
          {!useCurrentStart && (
            <PoseEditor title="Start" pose={pathStart} onChange={setPathStart} />
          )}
          <div className="row" style={{ gap: 8 }}>
            <label className="row" style={{ gap: 5, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={useHome}
                onChange={(event) => setUseHome(event.target.checked)}
              />
              Go via home
            </label>
            <button
              className="btn sm"
              onClick={() => {
                if (status?.pose) {
                  setPathHome({ ...status.pose })
                  setHomeRecorded(true)
                }
              }}
              disabled={!status?.pose}
            >
              Home = current
            </button>
            {homeRecorded && (
              <span className="faint" style={{ fontSize: 11 }}>
                home: {(pathHome.x * 1000).toFixed(0)},{' '}
                {(pathHome.y * 1000).toFixed(0)}, {(pathHome.z * 1000).toFixed(0)} mm
              </span>
            )}
          </div>
          {useHome && !homeRecorded && (
            <span className="faint" style={{ fontSize: 11.5 }}>
              Click <b>Home / fold</b> first, then <b>Home = current</b>.
            </span>
          )}
          {useHome && homeRecorded && (
            <PoseEditor title="Home" pose={pathHome} onChange={setPathHome} />
          )}
          <div className="row" style={{ gap: 8 }}>
            <label className="row" style={{ gap: 5, fontSize: 12 }}>
              <input
                type="checkbox"
                checked={useVia}
                onChange={(event) => setUseVia(event.target.checked)}
              />
              Use via point
            </label>
          </div>
          {useVia && <PoseEditor title="Via point" pose={pathVia} onChange={setPathVia} />}
          <p>Placement target: {pathTarget.x.toFixed(4)}, {pathTarget.y.toFixed(4)}, {pathTarget.z.toFixed(4)} m · box front-face centre · payload radius {(pathPayloadRadius * 1000).toFixed(0)} mm</p>
          <p className="faint" style={{ margin: 0, fontSize: 11.5 }}>
            Gripper attitude: Roll {placementOrientation(pathCage).roll.toFixed(1)}° · Pitch {placementOrientation(pathCage).pitch.toFixed(1)}° · Yaw {placementOrientation(pathCage).yaw.toFixed(1)}°
            {' '}(horizontal, aligned with cage depth). Attitude is verified before release (2° tolerance).
          </p>
          {twinTargetNote && (
            <p className="faint" style={{ margin: 0, fontSize: 11.5 }}>
              {twinTargetNote}
            </p>
          )}
          <div className="row" style={{ gap: 6 }}>
            <button className="btn sm" onClick={setTargetToCageCentre}>
              Target = cage floor centre
            </button>
            <span className="faint" style={{ fontSize: 11.5 }}>
              uses the current cage geometry · Z is 100 mm above the floor, edit it if needed
            </span>
          </div>
          <div className="row" style={{ gap: 6 }}>
            <NumberField
              label="Approach (m)"
              value={pathApproach}
              step={0.01}
              onChange={setPathApproach}
            />
            <NumberField
              label="Gate clearance (m)"
              value={pathGateClearance}
              step={0.01}
              onChange={setPathGateClearance}
            />
            {useVia && (
              <button
                className="btn sm"
                onClick={() => status?.pose && setPathVia({ ...status.pose })}
                disabled={!status?.pose}
              >
                Via = current
              </button>
            )}
            <button
              className="btn sm"
              onClick={() => void fillTargetFromQr()}
              disabled={!qr?.target_pose}
            >
              Target XYZ from QR
            </button>
          </div>

          <details>
            <summary className="muted" style={{ cursor: 'pointer', fontSize: 12.5 }}>
              Cage geometry (dimensions or 8 taught corners)
            </summary>
            <div className="stack" style={{ gap: 6, marginTop: 8 }}>
              <span className="muted" style={{ fontSize: 12 }}>
                Teach 8 corners (preferred; overrides dimensions)
              </span>
              <span className="faint" style={{ fontSize: 11.5 }}>
                Order: front-left-bottom, front-right-bottom, back-right-bottom,
                back-left-bottom, then the same four on top. The planner checks the 11 edges;
                the front-top edge is ignored because the cage is open there. BR top / BL top
                are unreachable, so they are ignored by default and derived from the other six.
              </span>
              <div className="grid cols-4" style={{ gap: 6 }}>
                {CORNER_LABELS.map((label, index) => {
                  const point = cageCorners[index]
                  const ignored = cageIgnored[index]
                  return (
                    <div key={label} className="stack" style={{ gap: 4 }}>
                      <span className="faint" style={{ fontSize: 11 }}>
                        {label}
                        {ignored ? ' · ignored' : ''}
                      </span>
                      <span
                        className="mono"
                        style={{ fontSize: 11, opacity: ignored ? 0.45 : 1 }}
                      >
                        {point
                          ? `${(point.x * 1000).toFixed(0)}, ${(point.y * 1000).toFixed(
                              0,
                            )}, ${(point.z * 1000).toFixed(0)} mm`
                          : '—'}
                      </span>
                      <button
                        className="btn sm"
                        onClick={() => recordCageCorner(index)}
                        disabled={!status?.pose || status.busy}
                      >
                        Record
                      </button>
                      <label
                        className="row"
                        style={{ gap: 4, fontSize: 10.5, color: 'var(--text-dim)' }}
                      >
                        <input
                          type="checkbox"
                          checked={ignored}
                          disabled={index < 6}
                          onChange={() => toggleCageIgnored(index)}
                        />
                        ignore
                      </label>
                    </div>
                  )
                })}
              </div>
              <div className="row" style={{ gap: 6 }}>
                <button
                  className="btn sm primary"
                  onClick={() => void fitCageCorners()}
                  disabled={
                    cageFitPending ||
                    cageCorners.some((point, index) => !point && !cageIgnored[index])
                  }
                >
                  {cageFitPending ? 'Fitting…' : 'Fit box from 8 corners'}
                </button>
                <button className="btn sm" onClick={clearCageCorners}>
                  Clear corners
                </button>
                {cageFit && (
                  <span className="faint" style={{ fontSize: 11.5 }}>
                    {cageFit.fit.width_m.toFixed(3)} × {cageFit.fit.depth_m.toFixed(3)} ×{' '}
                    {cageFit.fit.height_m.toFixed(3)} m · yaw{' '}
                    {cageFit.fit.yaw_deg.toFixed(1)}°
                  </span>
                )}
              </div>
              {cageFit && cageFit.fit.warnings.length > 0 && (
                <span style={{ color: 'var(--amber)', fontSize: 11.5 }}>
                  {cageFit.fit.warnings.join(' · ')}
                </span>
              )}
              {cageFit && (
                <span className="faint" style={{ fontSize: 11 }}>
                  Edge spread: W {Math.round(cageFit.fit.edge_spread_m.width_m * 1000)} mm · D{' '}
                  {Math.round(cageFit.fit.edge_spread_m.depth_m * 1000)} mm · H{' '}
                  {Math.round(cageFit.fit.edge_spread_m.height_m * 1000)} mm · orthogonality{' '}
                  {cageFit.fit.orthogonality.toFixed(2)}
                </span>
              )}
              {cageFit &&
                cageFit.fit.reconstructed_corners &&
                cageFit.fit.reconstructed_corners.length > 0 && (
                  <span className="faint" style={{ fontSize: 11 }}>
                    Derived:{' '}
                    {cageFit.fit.reconstructed_corners
                      .map((index) => CORNER_LABELS[index] ?? `#${index + 1}`)
                      .join(', ')}
                  </span>
                )}
              {cageFit &&
                cageFit.fit.reordered_corners &&
                cageFit.fit.reordered_corners.length > 0 && (
                  <span style={{ color: 'var(--amber)', fontSize: 11 }}>
                    Reordered:{' '}
                    {cageFit.fit.reordered_corners
                      .map((index) => CORNER_LABELS[index] ?? `#${index + 1}`)
                      .join(', ')}
                  </span>
                )}
              {cageFit && cageFit.fit.fit_method !== 'bottom_corners_plus_height' && (
                <span style={{ color: 'var(--amber)', fontSize: 11 }}>
                  Old cage fit; click Fit box from 8 corners again.
                </span>
              )}
            </div>
            <p className="faint" style={{ margin: '8px 0 0', fontSize: 11.5 }}>
              No ruler: jog the tool flange to the opening center, record it, then move straight
              back along the opening normal (~80 mm) and record the front point. Yaw and gate
              clearance are derived from the two points. Use the same reference for both records
              (flange, not gripper tip).
            </p>
            <div className="row" style={{ gap: 6, marginTop: 6 }}>
              <button
                className="btn sm"
                onClick={recordCageCenter}
                disabled={!status?.pose || status.busy}
              >
                Record center = current
              </button>
              <button
                className="btn sm"
                onClick={recordCageFront}
                disabled={!cageCenterRecorded || !status?.pose || status.busy}
              >
                Record front = current
              </button>
              {cageMessage && (
                <span className="faint" style={{ fontSize: 11.5 }}>
                  {cageMessage}
                </span>
              )}
            </div>
            {cageFront && (
              <span className="faint" style={{ fontSize: 11.5 }}>
                Front point: {cageFront.x.toFixed(3)}, {cageFront.y.toFixed(3)},{' '}
                {cageFront.z.toFixed(3)} m
              </span>
            )}
            <div className="row" style={{ gap: 6, marginTop: 8 }}>
              <NumberField
                label="Cage X (m)"
                value={pathCage.x}
                onChange={(value) => setPathCage((current) => ({ ...current, x: value }))}
              />
              <NumberField
                label="Cage Y (m)"
                value={pathCage.y}
                onChange={(value) => setPathCage((current) => ({ ...current, y: value }))}
              />
              <NumberField
                label="Cage Z (m)"
                value={pathCage.z}
                onChange={(value) => setPathCage((current) => ({ ...current, z: value }))}
              />
              <NumberField
                label="Yaw (°)"
                value={pathCage.yaw}
                step={1}
                onChange={(value) => setPathCage((current) => ({ ...current, yaw: value }))}
              />
            </div>
            <div className="row" style={{ gap: 6, marginTop: 6 }}>
              <NumberField
                label="Width (m)"
                value={pathCage.width}
                onChange={(value) => setPathCage((current) => ({ ...current, width: value }))}
              />
              <NumberField
                label="Depth (m)"
                value={pathCage.depth}
                onChange={(value) => setPathCage((current) => ({ ...current, depth: value }))}
              />
              <NumberField
                label="Height (m)"
                value={pathCage.height}
                onChange={(value) => setPathCage((current) => ({ ...current, height: value }))}
              />
              <NumberField
                label="Opening margin (m)"
                value={pathCage.opening_margin}
                onChange={(value) =>
                  setPathCage((current) => ({ ...current, opening_margin: value }))
                }
              />
              <NumberField
                label="Clearance (m)"
                value={pathCage.clearance_m}
                onChange={(value) =>
                  setPathCage((current) => ({ ...current, clearance_m: value }))
                }
              />
            </div>
          </details>

          <div className="row">
            <button
              className="btn primary"
              onClick={() => void planPickPath()}
              disabled={pathPending}
            >
              {pathPending ? 'Planning…' : 'Plan dry-run'}
            </button>
            {pathError && <span style={{ color: 'var(--red)', fontSize: 12 }}>{pathError}</span>}
          </div>

          <div
            className="stack"
            style={{
              gap: 6,
              borderTop: '1px solid var(--border-soft)',
              paddingTop: 10,
            }}
          >
            <span className="muted" style={{ fontSize: 12 }}>
              Execute (moves the arm)
            </span>
            <div className="row" style={{ gap: 6 }}>
              <NumberField
                label="Pull back (m)"
                value={execRetract}
                step={0.01}
                onChange={setExecRetract}
              />
              <NumberField
                label="Home lift (m)"
                value={execHomeLift}
                step={0.01}
                onChange={setExecHomeLift}
              />
              <span className="faint" style={{ fontSize: 11.5, alignSelf: 'center' }}>
                Speed {status?.max_velocity ?? 5}% (arm setting)
              </span>
              <label className="row" style={{ gap: 5, fontSize: 12 }}>
                <input
                  type="checkbox"
                  checked={returnHomeAfter}
                  onChange={(event) => setReturnHomeAfter(event.target.checked)}
                />
                Return home after
              </label>
              <button
                className="btn danger"
                onClick={() => void executePickPlace()}
                disabled={
                  execPending ||
                  !status?.connected ||
                  !!status?.fault ||
                  !!status?.busy ||
                  !!status?.learning_mode ||
                  !!status?.calibration_needed ||
                  !(status?.tool ?? '').includes('GRIPPER')
                }
              >
                {execPending ? 'Executing…' : 'Execute pick & place'}
              </button>
            </div>
            <span className="faint" style={{ fontSize: 11.5 }}>
              Uses the current pose as start, or the entered Start pose when “Start = current pose”
              is unchecked. Levels roll/pitch at the grasp point while keeping yaw free, closes the
              gripper, retracts horizontally, reorients at the outer retract point, then follows the
              checked path to the target and verifies attitude before opening. The recorded home pose
              is not used as an intermediate wrist-turning point.
            </span>
            {execError && (
              <span style={{ color: 'var(--red)', fontSize: 12 }}>{execError}</span>
            )}
            {execResult && (
              <table className="data">
                <thead>
                  <tr>
                    <th>Step</th>
                    <th>Result</th>
                    <th>Pose</th>
                  </tr>
                </thead>
                <tbody>
                  {execResult.steps.map((step, index) => (
                    <tr key={`${step.step}-${index}`}>
                      <td className="mono">
                        {step.step}
                        {step.angle_deg != null
                          ? ` · J2 ${step.angle_deg.toFixed(1)}°`
                          : ''}
                      </td>
                      <td>
                        <Badge tone={step.ok ? 'green' : 'red'}>
                          {step.ok ? 'ok' : 'fail'}
                        </Badge>
                      </td>
                      <td className="mono">
                        {step.pose
                          ? `${(step.pose.x * 1000).toFixed(0)}, ${(
                              step.pose.y * 1000
                            ).toFixed(0)}, ${(step.pose.z * 1000).toFixed(0)} mm`
                          : '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
            {execResult && execResult.plan && (
              <div className="stack" style={{ gap: 4 }}>
                {execResult.plan.warnings.length > 0 && (
                  <span className="faint" style={{ fontSize: 11.5 }}>
                    plan warnings: {execResult.plan.warnings.join(' · ')}
                  </span>
                )}
                {execResult.plan.segments.map((segment, index) => (
                  <span
                    key={`${segment.from}-${segment.to}-${index}`}
                    className="faint"
                    style={{
                      fontSize: 11.5,
                      color: segment.collision ? 'var(--red)' : undefined,
                    }}
                  >
                    {segment.from} → {segment.to}:{' '}
                    {segment.collision ? segment.issues.join('; ') : 'clear'}
                  </span>
                ))}
              </div>
            )}
          </div>

          {pathResult && (
            <div className="stack" style={{ gap: 8 }}>
              <span className="faint" style={{ fontSize: 11.5 }}>
                source: {pathResult.cage_source ?? 'dimensions'}
                {pathResult.cage_fit
                  ? ` · ${pathResult.cage_fit.width_m.toFixed(3)} × ${pathResult.cage_fit.depth_m.toFixed(3)} × ${pathResult.cage_fit.height_m.toFixed(3)} m · yaw ${pathResult.cage_fit.yaw_deg.toFixed(1)}°`
                  : ''}
              </span>
              {pathResult.actions && pathResult.actions.length > 0 && (
                <span className="faint" style={{ fontSize: 11.5 }}>
                  Actions:{' '}
                  {pathResult.actions
                    .map((action) => `${action.action} @ ${action.at}`)
                    .join(' · ')}
                </span>
              )}
              {pathResult.warnings.length > 0 && (
                <span className="faint" style={{ fontSize: 12 }}>
                  {pathResult.warnings.join(' · ')}
                </span>
              )}
              <table className="data">
                <thead>
                  <tr>
                    <th>Point</th>
                    <th>X / Y / Z (mm)</th>
                    <th>Zone</th>
                    <th>Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {pathResult.points.map((point, index) => (
                    <tr key={`${point.kind}-${index}`}>
                      <td className="mono">{point.kind}</td>
                      <td className="mono">
                        {(point.pose.x * 1000).toFixed(0)} /{' '}
                        {(point.pose.y * 1000).toFixed(0)} /{' '}
                        {(point.pose.z * 1000).toFixed(0)}
                      </td>
                      <td>
                        <Badge
                          tone={
                            point.zone === 'inside'
                              ? 'cyan'
                              : point.zone === 'gate'
                                ? 'amber'
                                : 'grey'
                          }
                        >
                          {point.zone}
                        </Badge>
                      </td>
                      <td className="faint" style={{ fontSize: 11.5 }}>
                        {point.issues.join('; ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              <table className="data">
                <thead>
                  <tr>
                    <th>Segment</th>
                    <th>Length</th>
                    <th>Min clearance</th>
                    <th>Result</th>
                    <th>Issues</th>
                  </tr>
                </thead>
                <tbody>
                  {pathResult.segments.map((segment, index) => (
                    <tr key={`${segment.from}-${segment.to}-${index}`}>
                      <td className="mono">
                        {segment.from} → {segment.to}
                      </td>
                      <td className="mono">{(segment.length_m * 1000).toFixed(0)} mm</td>
                      <td className="mono">
                        {segment.min_clearance_m != null
                          ? `${(segment.min_clearance_m * 1000).toFixed(0)} mm`
                          : '—'}
                      </td>
                      <td>
                        <Badge tone={segment.collision ? 'red' : 'green'}>
                          {segment.collision ? 'collision' : 'clear'}
                        </Badge>
                      </td>
                      <td className="faint" style={{ fontSize: 11.5 }}>
                        {segment.issues.join('; ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </Card>

      {status?.fault && (
        <div className="error-banner">
          <div>
            <strong>{status.collision ? 'Collision / motion fault' : 'Motion fault'}.</strong>{' '}
            {status.fault}
          </div>
          <div className="row" style={{ marginTop: 10, alignItems: 'center' }}>
            <button
              className="btn sm primary"
              onClick={recoverArm}
              disabled={pending || status.busy || !status.recovery_available}
            >
              {pending ? 'Working…' : 'Recover to last safe point'}
            </button>
            <button className="btn sm" onClick={clearFault} disabled={pending || status.busy}>
              Clear after inspection
            </button>
            <span className="faint" style={{ fontSize: 12 }}>
              {status.recovery_available
                ? 'Slow reverse move to the last confirmed-safe joint position.'
                : 'No retract point available; release the motors and move the arm clear by hand.'}
            </span>
          </div>
        </div>
      )}

      {status?.calibration_needed && (
        <div className="error-banner">
          Calibration is required. Motion is disabled until initialization finishes. Use{' '}
          <strong>Initialize / auto-calibrate</strong> below (or run{' '}
          <span className="mono">../arm/ned_calibrate.py --go</span>).
        </div>
      )}

      {status?.learning_mode && (
        <div className="stale-warning">
          Motors are released (learning mode). Hold the arm before re-engaging.
          <button className="btn sm" onClick={enableMotors} disabled={pending || status.busy}>
            Enable motors
          </button>
        </div>
      )}

      {sequenceRun && (
        <div className="stale-warning">
          <span>
            Running sequence <b>{sequenceRun.name}</b> — step {sequenceRun.step}/{sequenceRun.total}{' '}
            ({sequenceRun.phase === 'transit' ? 'transit pose' : sequenceRun.phase === 'gripper' ? 'gripper' : 'pose'}). Stop takes effect after the current step
            finishes.
          </span>
          <button className="btn sm danger" onClick={stopSequence} disabled={sequenceStopping}>
            {sequenceStopping ? 'Stopping after current move…' : 'Stop sequence'}
          </button>
        </div>
      )}

      {error && !status?.fault && <div className="error-banner">{error}</div>}

      <div className="grid cols-2">
        <Card title="Connection" hint="PyNiryo TCP :40001 · one client at a time">
          <div className="stack">
            <div className="field">
              <label>Ned1 address</label>
              <input value={host} onChange={(e) => setHost(e.target.value)} placeholder="10.10.10.10" spellCheck={false} />
            </div>
            <div className="row">
              {HOSTS.map((h) => (
                <button
                  key={h.ip}
                  className={`btn sm ${host === h.ip ? 'primary' : ''}`}
                  onClick={() => setHost(h.ip)}
                >
                  {h.label} · <span className="mono">{h.ip}</span>
                </button>
              ))}
            </div>
            {host === '127.0.0.1' && (
              <span className="faint" style={{ fontSize: 11.5 }}>
                Requires an SSH tunnel on the control machine: local :40001 → Jetson → arm :40001.
              </span>
            )}
            <div className="row">
              <button className="btn primary" onClick={connect} disabled={pending || !!status?.connected}>
                {pending ? 'Working…' : 'Connect'}
              </button>
              <button className="btn danger" onClick={disconnect} disabled={pending || !status?.connected}>
                Disconnect
              </button>
            </div>
            <div className="stack" style={{ gap: 6, fontSize: 12.5, paddingTop: 10, borderTop: '1px solid var(--border-soft)' }}>
              <div className="row between">
                <span className="muted">Initialization</span>
                <span className="mono">
                  {status?.connected ? (status.calibration_needed ? 'required' : 'done') : '—'}
                </span>
              </div>
              <label className="row" style={{ fontSize: 12.5, color: 'var(--text-dim)', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={confirmCalibration}
                  onChange={(e) => setConfirmCalibration(e.target.checked)}
                  disabled={!status?.connected || pending || status?.busy}
                  style={{ width: 'auto', accentColor: 'var(--accent)' }}
                />
                Workspace clear (≥0.5 m); the arm will sweep its full range
              </label>
              <button
                className="btn danger"
                onClick={() => void calibrate()}
                disabled={!status?.connected || !confirmCalibration || pending || status?.busy}
              >
                {pending ? 'Working…' : 'Initialize / auto-calibrate'}
              </button>
              <span className="faint" style={{ fontSize: 11.5 }}>
                Slow full-range sweep, roughly 30 s. No hardware E-stop.
              </span>
            </div>
            <div className="stack" style={{ gap: 6, fontSize: 12.5 }}>
              <div className="row between">
                <span className="muted">Mode</span>
                <span className="mono">{status?.mock ? 'mock' : 'hardware'}</span>
              </div>
              <div className="row between">
                <span className="muted">Speed limit</span>
                <select
                  value={status?.max_velocity ?? 5}
                  onChange={(e) => setSpeed(Number(e.target.value))}
                  disabled={pending || status?.busy}
                  style={{ maxWidth: 100 }}
                >
                  {speedOptions.map((value) => (
                    <option key={value} value={value}>
                      {value}%
                    </option>
                  ))}
                </select>
              </div>
              <div className="row between">
                <span className="muted">Max reach</span>
                <select
                  value={status?.max_reach_m ?? 0.47}
                  onChange={(e) => setReach(Number(e.target.value))}
                  disabled={pending || status?.busy}
                  style={{ maxWidth: 100 }}
                >
                  {reachOptions.map((value) => (
                    <option key={value} value={value}>
                      {value.toFixed(2)} m
                    </option>
                  ))}
                </select>
              </div>
              <div className="row between">
                <span className="muted">Per-command limit</span>
                <span className="mono">≤{status?.max_request_deg ?? 60}°</span>
              </div>
              <div className="row between">
                <span className="muted">Calibration</span>
                <span className="mono">
                  {status?.connected ? (status.calibration_needed ? 'required' : 'ok') : '—'}
                </span>
              </div>
              <div className="row between">
                <span className="muted">Collision detect</span>
                <span className="mono">
                  {status?.collision_api === 'hardware'
                    ? 'firmware flag'
                    : status?.collision_api === 'motion-error'
                      ? 'motion error'
                      : 'probing'}
                </span>
              </div>
            </div>
            <div className="faint" style={{ fontSize: 11.5 }}>
              Max reach is measured at the gripper tip (tool frame + {status?.tool_extension_m ?? 0.1} m).
              Joint-path moves are rejected if the arc exceeds it; linear pose moves check the endpoints.
            </div>
          </div>
        </Card>

        <Card title="Cartesian pose" hint="read-only · millimetres and degrees">
          {status?.pose ? (
            <div className="stack" style={{ gap: 6, fontSize: 12.5 }}>
              <div className="row between">
                <span className="muted">X / Y / Z</span>
                <span className="mono">
                  {(status.pose.x * 1000).toFixed(0)} / {(status.pose.y * 1000).toFixed(0)} /{' '}
                  {(status.pose.z * 1000).toFixed(0)} mm
                </span>
              </div>
              <div className="row between">
                <span className="muted">Horizontal reach</span>
                <span
                  className="mono"
                  style={{
                    color:
                      status.reach_m != null && status.reach_m > (status.max_reach_m ?? Infinity)
                        ? 'var(--red)'
                        : undefined,
                  }}
                >
                  {status.reach_m != null ? `${status.reach_m.toFixed(3)} m` : '—'}
                </span>
              </div>
              <div className="row between">
                <span className="muted">Roll / Pitch / Yaw</span>
                <span className="mono">
                  {status.pose.roll.toFixed(1)} / {status.pose.pitch.toFixed(1)} / {status.pose.yaw.toFixed(1)}°
                </span>
              </div>
              <div className="row between">
                <span className="muted">Joints</span>
                <span className="mono">
                  {status.joints_deg?.map((v) => v.toFixed(1)).join(' · ') ?? '—'}
                </span>
              </div>
              <div className="row between">
                <span className="muted">Updated</span>
                <span className="mono">
                  {status.updated_at ? new Date(status.updated_at * 1000).toLocaleTimeString('en-GB') : '—'}
                </span>
              </div>
            </div>
          ) : (
            <div className="empty">Connect to read the arm pose.</div>
          )}
        </Card>
      </div>

      <Card title="Tool & manual pose" hint="gripper and free-motion mode">
        <div className="grid cols-2">
          <div className="stack">
            <div className="row between">
              <span className="muted">Detected tool</span>
              <Badge tone={hasGripper ? 'green' : 'amber'}>
                {status?.tool ? status.tool.replace('_', ' ') : 'none'}
              </Badge>
            </div>
            <div className="row">
              <button className="btn" onClick={openGripper} disabled={!canUseGripper}>
                Open
              </button>
              <button className="btn primary" onClick={closeGripper} disabled={!canUseGripper}>
                Close / grasp
              </button>
              <button className="btn sm" onClick={scanTool} disabled={!status?.connected || pending || status?.busy}>
                Rescan tool
              </button>
            </div>
            <div className="row">
              <button className="btn sm" onClick={() => rawGripper(true)} disabled={!canUseGripper}>
                Raw open
              </button>
              <button className="btn sm" onClick={() => rawGripper(false)} disabled={!canUseGripper}>
                Raw close
              </button>
              <span className="faint" style={{ fontSize: 11.5 }}>
                firmware 1-param test
              </span>
            </div>
            <div className="row between" style={{ fontSize: 12.5 }}>
              <span className="muted">Gripper state</span>
              <span className="mono">{status?.gripper ?? 'unknown'}</span>
            </div>
            <p className="faint" style={{ fontSize: 12, margin: 0 }}>
Uses the robot's native grasp/release tool commands. Buttons block while the gripper moves.
            </p>
          </div>

          <div className="stack">
            <div className="row between">
              <span className="muted">Motors</span>
              <Badge tone={status?.learning_mode ? 'amber' : status?.connected ? 'green' : 'grey'} dot>
                {status?.learning_mode ? 'released' : 'engaged'}
              </Badge>
            </div>
            <label className="row" style={{ fontSize: 12.5, color: 'var(--text-dim)', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={confirmRelease}
                onChange={(e) => setConfirmRelease(e.target.checked)}
                disabled={!status?.connected || pending || !!status?.learning_mode || status?.busy}
                style={{ width: 'auto', accentColor: 'var(--accent)' }}
              />
              I am holding the arm — releasing motors lets it fall
            </label>
            <button
              className="btn danger"
              onClick={() => void releaseMotors()}
              disabled={!status?.connected || !confirmRelease || pending || !!status?.learning_mode || status?.busy}
            >
              Manual pose (release motors)
            </button>
            <button
              className="btn"
              onClick={enableMotors}
              disabled={!status?.learning_mode || pending || status?.busy}
            >
              Lock position (enable motors)
            </button>
            <p className="faint" style={{ fontSize: 12, margin: 0 }}>
              Pose the arm by hand, then lock it to hold the new position.
            </p>
          </div>
        </div>
      </Card>

      <Card
        title="Pose presets"
        hint={`saved on the arm bridge · recall moves ≤${status?.max_pose_deg ?? 180}°`}
      >
        <div className="stack">
          <div className="row">
            <input
              value={poseName}
              onChange={(e) => setPoseName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !pending) void savePose()
              }}
              placeholder="pose name, e.g. pickup-high"
              style={{ maxWidth: 300 }}
              spellCheck={false}
            />
            <button
              className="btn primary"
              onClick={() => void savePose()}
              disabled={!status?.connected || !status?.joints_deg || pending || status?.busy || !poseName.trim()}
            >
              Save current pose
            </button>
            <label className="row" style={{ gap: 6, fontSize: 12, color: 'var(--text-dim)', cursor: 'pointer' }}>
              <input
                type="checkbox"
                checked={linearPath}
                onChange={(e) => setLinearPath(e.target.checked)}
                disabled={pending || status?.busy || !!sequenceRun}
                style={{ width: 'auto', accentColor: 'var(--accent)' }}
              />
              Linear path
            </label>
            <span className="faint" style={{ fontSize: 12 }}>
              Same name overwrites; saving during manual pose captures the hand-posed position.
            </span>
          </div>

          {poses.length === 0 ? (
            <div className="empty">No saved poses yet.</div>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>Joints °</th>
                  <th style={{ width: 100 }}>Reach</th>
                  <th style={{ width: 190 }}>Saved</th>
                  <th style={{ width: 150 }} />
                </tr>
              </thead>
              <tbody>
                {poses.map((pose) => (
                  <tr key={pose.name}>
                    <td className="mono">{pose.name}</td>
                    <td className="faint mono" style={{ fontSize: 11.5 }}>
                      {pose.joints_deg.map((v) => v.toFixed(1)).join(' · ')}
                    </td>
                    <td
                      className="mono"
                      style={{
                        fontSize: 11.5,
                        color:
                          pose.radius_m > (status?.max_reach_m ?? Infinity) ? 'var(--red)' : 'var(--text-faint)',
                      }}
                    >
                      {pose.radius_m ? `${pose.radius_m.toFixed(2)} m` : '—'}
                    </td>
                    <td className="faint mono" style={{ fontSize: 11.5 }}>
                      {new Date(pose.updated_at ?? pose.created_at ?? Date.now()).toLocaleString('en-GB')}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        <button className="btn sm primary" onClick={() => applyPose(pose.name)} disabled={!canMove}>
                          Go
                        </button>
                        <button
                          className="btn sm danger"
                          onClick={() => void deletePose(pose.name)}
                          disabled={pending || status?.busy}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <p className="faint" style={{ fontSize: 12, margin: 0 }}>
            Linear path moves the gripper in a straight Cartesian line and avoids the outward arc; joint path uses
            60° joint segments and is rejected if the arc exceeds Max reach. Recall runs at the current speed cap.
          </p>
        </div>
      </Card>

      <Card
        title="Sequences"
        hint={`ordered pose + gripper playback · ≤${status?.max_pose_deg ?? 180}° per step · runs in this browser`}
      >
        <div className="stack">
          <div className="row">
            <input
              value={sequenceName}
              onChange={(e) => setSequenceName(e.target.value)}
              placeholder="sequence name, e.g. pick-cycle"
              style={{ maxWidth: 280 }}
              spellCheck={false}
            />
            <label className="row" style={{ gap: 6 }}>
              <span className="faint" style={{ fontSize: 12 }}>
                Dwell (s)
              </span>
              <input
                type="number"
                min={0}
                max={10}
                step={0.5}
                value={dwellSec}
                onChange={(e) => {
                  const value = Number(e.target.value)
                  setDwellSec(Number.isFinite(value) ? Math.max(0, Math.min(10, value)) : 0)
                }}
                style={{ maxWidth: 90 }}
              />
            </label>
            <label className="row" style={{ gap: 6 }}>
              <span className="faint" style={{ fontSize: 12 }}>
                Transit
              </span>
              <select
                value={transitPose}
                onChange={(e) => setTransitPose(e.target.value)}
                disabled={pending || status?.busy || !!sequenceRun}
                style={{ maxWidth: 220 }}
              >
                <option value="">(none · direct)</option>
                <option value="__home__">Home / fold (built-in)</option>
                {poses.map((pose) => (
                  <option key={pose.name} value={pose.name}>
                    {pose.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              className="btn primary"
              onClick={() => void saveSequence()}
              disabled={!draftSteps.length || !sequenceName.trim() || pending || status?.busy}
            >
              Save sequence
            </button>
            <button
              className="btn sm"
              onClick={() => {
                setSequenceName('')
                setDraftSteps([])
                setTransitPose('')
              }}
              disabled={pending || status?.busy || !!sequenceRun}
            >
              Clear draft
            </button>
          </div>

          <div className="row">
            <select
              value={addableStep}
              onChange={(e) => setStepToAdd(e.target.value)}
              disabled={pending || status?.busy || !!sequenceRun}
              style={{ maxWidth: 220 }}
            >
              <optgroup label="Gripper">
                <option value={ARM_SEQUENCE_GRIPPER_CLOSE}>Gripper: close</option>
                <option value={ARM_SEQUENCE_GRIPPER_OPEN}>Gripper: open</option>
              </optgroup>
              {poses.length > 0 && (
                <optgroup label="Poses">
                  {poses.map((pose) => (
                    <option key={pose.name} value={pose.name}>
                      {pose.name}
                    </option>
                  ))}
                </optgroup>
              )}
            </select>
            <button
              className="btn"
              onClick={() => addDraftStep()}
              disabled={pending || status?.busy || !!sequenceRun}
            >
              Add step
            </button>
            <button
              className="btn sm"
              onClick={() => addDraftStep(ARM_SEQUENCE_GRIPPER_CLOSE)}
              disabled={pending || status?.busy || !!sequenceRun}
            >
              + Close
            </button>
            <button
              className="btn sm"
              onClick={() => addDraftStep(ARM_SEQUENCE_GRIPPER_OPEN)}
              disabled={pending || status?.busy || !!sequenceRun}
            >
              + Open
            </button>
            <span className="faint" style={{ fontSize: 12 }}>
              Gripper steps act at the current pose; transit is skipped for them.
            </span>
          </div>

          {draftSteps.length === 0 ? (
            <div className="empty">Draft is empty.</div>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th style={{ width: 60 }}>#</th>
                  <th>Step</th>
                  <th style={{ width: 230 }}>Order</th>
                </tr>
              </thead>
              <tbody>
                {draftSteps.map((step, index) => (
                  <tr key={`${step}-${index}`}>
                    <td className="mono">{index + 1}</td>
                    <td className="mono">{sequenceStepLabel(step)}</td>
                    <td>
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        <button
                          className="btn sm"
                          onClick={() => moveDraftStep(index, -1)}
                          disabled={index === 0 || pending || status?.busy || !!sequenceRun}
                        >
                          Up
                        </button>
                        <button
                          className="btn sm"
                          onClick={() => moveDraftStep(index, 1)}
                          disabled={index === draftSteps.length - 1 || pending || status?.busy || !!sequenceRun}
                        >
                          Down
                        </button>
                        <button
                          className="btn sm danger"
                          onClick={() => removeDraftStep(index)}
                          disabled={pending || status?.busy || !!sequenceRun}
                        >
                          Remove
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <div className="nav-section" style={{ padding: '10px 0 4px' }}>
            Saved sequences
          </div>
          {sequences.length === 0 ? (
            <div className="empty">No saved sequences yet.</div>
          ) : (
            <table className="data">
              <thead>
                <tr>
                  <th>Name</th>
                  <th style={{ width: 80 }}>Steps</th>
                  <th style={{ width: 90 }}>Dwell</th>
                  <th style={{ width: 150 }}>Transit</th>
                  <th style={{ width: 190 }}>Saved</th>
                  <th style={{ width: 240 }} />
                </tr>
              </thead>
              <tbody>
                {sequences.map((sequence) => (
                  <tr key={sequence.name}>
                    <td className="mono">{sequence.name}</td>
                    <td className="mono">{sequence.poses.length}</td>
                    <td className="mono">{sequence.dwell_sec.toFixed(1)}s</td>
                    <td className="faint mono" style={{ fontSize: 11.5 }}>
                      {sequence.transit === '__home__' ? 'Home / fold' : (sequence.transit ?? 'direct')}
                    </td>
                    <td className="faint mono" style={{ fontSize: 11.5 }}>
                      {new Date(sequence.updated_at ?? sequence.created_at ?? Date.now()).toLocaleString('en-GB')}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                        <button
                          className="btn sm primary"
                          onClick={() => void runSequence(sequence)}
                          disabled={!canMove || !!sequenceRun}
                        >
                          Run
                        </button>
                        <button
                          className="btn sm"
                          onClick={() => loadSequence(sequence)}
                          disabled={pending || status?.busy || !!sequenceRun}
                        >
                          Load
                        </button>
                        <button
                          className="btn sm danger"
                          onClick={() => void deleteSequence(sequence.name)}
                          disabled={pending || status?.busy || !!sequenceRun}
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <p className="faint" style={{ fontSize: 12, margin: 0 }}>
            The transit pose is applied before pose steps only; gripper steps act at the current pose. Use a
            raised/centre transit to avoid sweeping low across the table. Stop is checked between steps; the current
            blocking move always finishes.
          </p>
        </div>
      </Card>

      <Card
        title="Joint control"
        hint={controlMode === 'joint' ? 'Drag a slider and release, or nudge/apply targets; each release moves ≤60°' : 'Hold a slider or key; release to stop; position and attitude update in real time'}
        right={
          <div className="row">
            <span className="faint" style={{ fontSize: 12 }}>Mode</span>
            <button className={`btn sm ${controlMode === 'joint' ? 'primary' : ''}`} onClick={() => setControlMode('joint')}>Joint</button>
            <button className={`btn sm ${controlMode === 'linear' ? 'primary' : ''}`} onClick={() => setControlMode('linear')}>Linear</button>
            {controlMode === 'joint' && (
              <>
                <span className="faint" style={{ fontSize: 12 }}>Step</span>
                {[1, 2, 5, 10].map((s) => (
                  <button key={s} className={`btn sm ${step === s ? 'primary' : ''}`} onClick={() => setStep(s)}>
                    {s}°
                  </button>
                ))}
                <button className="btn sm" onClick={syncTargets} disabled={!status?.joints_deg}>
                  Sync targets
                </button>
              </>
            )}
          </div>
        }
      >
        <table className="data" style={{ display: controlMode === 'joint' ? undefined : 'none' }}>
          <thead>
            <tr>
              <th>Joint</th>
              <th style={{ width: 90 }}>Current</th>
              <th style={{ minWidth: 180 }}>Drag</th>
              <th style={{ width: 120 }}>Target °</th>
              <th style={{ width: 100 }}>Nudge</th>
              <th style={{ width: 140 }}>Limits</th>
            </tr>
          </thead>
          <tbody>
            {JOINTS.map((joint, i) => {
              const current = status?.joints_deg?.[i]
              const limits = status?.limits_deg?.[i]
              return (
                <tr key={joint.id}>
                  <td>
                    <span className="tag">{joint.id}</span> <span className="faint">{joint.name}</span>
                  </td>
                  <td className="mono">{current === undefined ? '—' : `${current.toFixed(1)}°`}</td>
                  <td>
                    <input
                      type="range"
                      min={limits ? limits[0] : -180}
                      max={limits ? limits[1] : 180}
                      step={0.5}
                      value={Number.isFinite(Number(targets[i])) ? Number(targets[i]) : 0}
                      onChange={(e) => {
                        const next = [...targets]
                        next[i] = e.target.value
                        setTargets(next)
                      }}
                      onPointerUp={(e) => commitJoint(i, Number(e.currentTarget.value))}
                      onKeyUp={(e) => {
                        if (e.key.startsWith('Arrow') || e.key === 'Home' || e.key === 'End') {
                          commitJoint(i, Number(e.currentTarget.value))
                        }
                      }}
                      disabled={!canMove}
                      aria-label={`${joint.id} target angle`}
                    />
                  </td>
                  <td>
                    <input
                      type="number"
                      step="0.1"
                      value={targets[i]}
                      onChange={(e) => {
                        const next = [...targets]
                        next[i] = e.target.value
                        setTargets(next)
                      }}
                      disabled={!canMove}
                    />
                  </td>
                  <td>
                    <div className="row" style={{ gap: 6, flexWrap: 'nowrap' }}>
                      <button className="btn sm" onClick={() => jog(i, -1)} disabled={!canMove}>
                        −
                      </button>
                      <button className="btn sm" onClick={() => jog(i, 1)} disabled={!canMove}>
                        +
                      </button>
                    </div>
                  </td>
                  <td className="faint mono" style={{ fontSize: 11.5 }}>
                    {limits ? `${limits[0].toFixed(1)} … ${limits[1].toFixed(1)}°` : '—'}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
        {controlMode === 'linear' && (
          <div className="stack" style={{ gap: 14 }}>
            <div className="grid cols-2" style={{ gap: 20 }}>
              <div className="stack" style={{ gap: 8 }}>
                <div className="row between">
                  <span className="muted" style={{ fontSize: 12 }}>Position · hold to move</span>
                  <label className="row" style={{ gap: 5, fontSize: 11 }}>
                    <span className="faint">Speed</span>
                    <select
                      value={linearSpeedScale}
                      onChange={(event) => setLinearSpeedScale(Number(event.target.value))}
                      style={{ width: 64 }}
                    >
                      {[1, 2, 3, 4, 5, 6].map((value) => (
                        <option key={value} value={value}>{value}×</option>
                      ))}
                    </select>
                  </label>
                </div>
                {([
                  { key: 'z', label: 'Down / Up' },
                  { key: 'x', label: 'Left / Right' },
                  { key: 'y', label: 'Back / Forward' },
                ] as const).map(({ key, label }) => (
                  <div key={key} className="row" style={{ gap: 8 }}>
                    <span className="faint" style={{ width: 100, fontSize: 11.5 }}>{label}</span>
                    <span className="faint" style={{ fontSize: 11 }}>−</span>
                    <input
                      type="range"
                      min={-1}
                      max={1}
                      step={0.05}
                      value={linearSliders[key]}
                      onPointerDown={startLinearAxis}
                      onChange={(event) => setLinearAxis(key, Number(event.target.value))}
                      onPointerUp={() => stopLinearAxis(key)}
                      onPointerCancel={() => stopLinearAxis(key)}
                      onBlur={() => stopLinearAxis(key)}
                      disabled={!canMove}
                      style={{ flex: 1, accentColor: 'var(--accent)' }}
                      aria-label={`${label} real-time axis`}
                    />
                    <span className="faint" style={{ fontSize: 11 }}>+</span>
                  </div>
                ))}
                <span className="faint" style={{ fontSize: 11.5 }}>
                  Keyboard: W/S forward-back · A/D left-right · Q/E down-up · U/O yaw · I/K pitch · J/L roll · N/M J6 · G/H gripper.
                </span>
              </div>

              <div className="stack" style={{ gap: 6, alignItems: 'center' }}>
                <span className="muted" style={{ fontSize: 12 }}>Gripper direction · drag the sphere</span>
                <div
                  style={{
                    width: 210,
                    height: 210,
                    borderRadius: '50%',
                    position: 'relative',
                    touchAction: 'none',
                    cursor: canMove ? 'grab' : 'not-allowed',
                    border: '1px solid var(--border)',
                    background:
                      'radial-gradient(circle at 35% 28%, rgba(255,255,255,0.14), transparent 38%), radial-gradient(circle at 50% 50%, #141b2a 0%, #0a0f18 72%)',
                    boxShadow: 'inset 0 0 28px rgba(0,0,0,.55)',
                  }}
                  onPointerDown={startOrientationPad}
                  onPointerMove={moveOrientationPad}
                  onPointerUp={stopOrientationPad}
                  onPointerCancel={stopOrientationPad}
                  onPointerLeave={stopOrientationPad}
                  role="slider"
                  tabIndex={0}
                  aria-label="gripper pitch and yaw direction"
                >
                  <div style={{ position: 'absolute', left: '8%', right: '8%', top: '50%', borderTop: '1px solid rgba(255,255,255,.18)' }} />
                  <div style={{ position: 'absolute', top: '8%', bottom: '8%', left: '50%', borderLeft: '1px solid rgba(255,255,255,.18)' }} />
                  <div style={{ position: 'absolute', left: '15%', right: '15%', top: '50%', height: '70%', transform: 'translateY(-50%)', border: '1px solid rgba(255,255,255,.12)', borderRadius: '50%' }} />
                  <div
                    style={{
                      position: 'absolute',
                      left: `calc(50% + ${orientationPad.x * 42}% - 7px)`,
                      top: `calc(50% + ${orientationPad.y * 42}% - 7px)`,
                      width: 14,
                      height: 14,
                      borderRadius: '50%',
                      background: 'var(--accent)',
                      boxShadow: '0 0 12px rgba(74, 222, 255, .8)',
                    }}
                  />
                  <span className="faint" style={{ position: 'absolute', top: 3, left: 0, right: 0, textAlign: 'center', fontSize: 10 }}>pitch +</span>
                  <span className="faint" style={{ position: 'absolute', bottom: 3, left: 0, right: 0, textAlign: 'center', fontSize: 10 }}>pitch −</span>
                  <span className="faint" style={{ position: 'absolute', left: 4, top: '50%', transform: 'translateY(-50%)', fontSize: 10 }}>yaw −</span>
                  <span className="faint" style={{ position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)', fontSize: 10 }}>yaw +</span>
                </div>
                <span className="faint" style={{ fontSize: 11.5 }}>
                  Sphere drag controls pitch/yaw; keyboard U/O yaw, I/K pitch, J/L roll, N/M J6.
                </span>
              </div>
            </div>

            <div className="stack" style={{ gap: 6, borderTop: '1px solid var(--border-soft)', paddingTop: 10 }}>
              <div className="row between">
                <span className="muted" style={{ fontSize: 12 }}>J6 wrist roll · separate gripper rotation</span>
                <span className="mono" style={{ fontSize: 12 }}>{j6Target.toFixed(1)}°</span>
              </div>
              <div className="row" style={{ gap: 8 }}>
                <span className="faint" style={{ fontSize: 11 }}>{j6Limits[0].toFixed(1)}°</span>
                <input
                  type="range"
                  min={j6Limits[0]}
                  max={j6Limits[1]}
                  step={0.5}
                  value={j6Target}
                  onChange={(event) => updateJ6(Number(event.target.value))}
                  onPointerUp={(event) => commitJ6(Number(event.currentTarget.value))}
                  onPointerCancel={() => { j6DraggingRef.current = false }}
                  onKeyUp={(event) => {
                    if (event.key.startsWith('Arrow') || event.key === 'Home' || event.key === 'End') {
                      commitJ6(Number(event.currentTarget.value))
                    }
                  }}
                  disabled={!canMove}
                  style={{ flex: 1, accentColor: 'var(--accent)' }}
                  aria-label="J6 wrist roll"
                />
                <span className="faint" style={{ fontSize: 11 }}>{j6Limits[1].toFixed(1)}°</span>
              </div>
              <span className="faint" style={{ fontSize: 11.5 }}>
                J6 has a hardware/firmware range of about {j6Limits[0].toFixed(0)}° to {j6Limits[1].toFixed(0)}°,
                so continuous 360° rotation is not physically available.
              </span>
            </div>

            <div className="row">
              <button className="btn danger" onClick={stopMotion}>
                STOP motion / clear inputs
              </button>
            </div>
            <span className="faint" style={{ fontSize: 11.5 }}>
              {status?.pose
                ? `Current attitude: roll ${status.pose.roll.toFixed(1)}° · pitch ${status.pose.pitch.toFixed(1)}° · yaw ${status.pose.yaw.toFixed(1)}°`
                : 'Connect the arm to enable real-time control.'}
            </span>
          </div>
        )}
        <div className="row" style={{ marginTop: 12, display: controlMode === 'joint' ? undefined : 'none' }}>
          <button className="btn primary" onClick={applyTargets} disabled={!canMove}>
            Apply all targets
          </button>
          <button className="btn" onClick={homeArm} disabled={!canMove}>
            Home / fold
          </button>
          <span className="faint" style={{ fontSize: 12 }}>
            Drag sliders commit on release. Manual moves are capped at 60° per command.
          </span>
        </div>
      </Card>

      <Card title="Arm log" hint="this browser session">
        {log.length === 0 ? (
          <div className="empty">No arm commands yet.</div>
        ) : (
          log.map((entry) => (
            <div className="activity" key={entry.id}>
              <span className="t">{clock(entry.t)}</span>
              <span className={`lv-${entry.level}`}>●</span>
              <span>{entry.text}</span>
            </div>
          ))
        )}
      </Card>
    </>
  )
}
