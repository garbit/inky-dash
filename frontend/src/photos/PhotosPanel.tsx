import { useCallback, useEffect, useState } from 'react'
import {
  clearPhotos,
  deleteGoogle,
  deletePhoto,
  getGoogleStatus,
  getPhotoJobs,
  getPhotos,
  postDriveSync,
  postGoogleComplete,
  postGoogleConnect,
  postPickerCancel,
  postPickerSession,
  putPhotoFocus,
  uploadPhotos,
} from '../api'
import type { GoogleServiceStatus, ModuleSection, PhotoEntry, PhotoJobs, PhotoPool } from '../types'
import PhotoFramer, { focusOf, isCentred } from './PhotoFramer'

// Poll briskly only while something is actually running. Idling at the busy rate
// pointlessly wakes a Pi Zero several times a minute for a "nothing happened".
const JOB_POLL_BUSY_MS = 3000
const JOB_POLL_IDLE_MS = 30000

interface Props {
  values: ModuleSection
  onPatch: (patch: ModuleSection) => void
}

/** Manages the photo pool the panel draws from: the Google connections, Google's
 * photo picker, a Drive folder to mirror, direct uploads, and what's stored.
 *
 * Fetches its own data rather than taking it as a prop — the pool and the jobs
 * are server-side state that changes underneath the config draft. The picking
 * and syncing themselves run on the backend; this only starts them and watches.
 */
export default function PhotosPanel({ values, onPatch }: Props) {
  const [google, setGoogle] = useState<Record<string, GoogleServiceStatus>>({})
  const [hasCredentials, setHasCredentials] = useState(true)
  const [project, setProject] = useState<string | null>(null)
  const [pool, setPool] = useState<PhotoPool | null>(null)
  const [jobs, setJobs] = useState<PhotoJobs | null>(null)
  const [message, setMessage] = useState<string | null>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [pending, setPending] = useState<{ state: string } | null>(null)
  const [pasted, setPasted] = useState('')
  const [framing, setFraming] = useState<PhotoEntry | null>(null)

  const refresh = useCallback(async () => {
    const [status, photos] = await Promise.all([getGoogleStatus(), getPhotos()])
    setGoogle(status.services)
    setHasCredentials(status.credentials)
    setProject(status.project)
    setPool(photos)
  }, [])

  useEffect(() => {
    refresh().catch(() => setMessage('Could not reach the backend.'))
  }, [refresh])

  // Watch the background jobs. This runs on mount too, so opening the config UI
  // while a pick is in flight — or after picking on a phone — shows its progress
  // rather than pretending nothing is happening.
  useEffect(() => {
    let stop = false
    let timer: ReturnType<typeof setTimeout> | undefined
    let previous: string | null = null

    // A self-scheduling chain rather than setInterval: each request is sent only
    // after the previous one has come back, so a slow reply can never stack up
    // into a queue of overlapping polls.
    const tick = async () => {
      if (stop) return
      let busy = false
      if (document.hidden) {
        // A background tab (there are usually a few left over from signing in)
        // has no reason to ask; onVisible catches up the moment it is looked at.
        timer = setTimeout(tick, JOB_POLL_IDLE_MS)
        return
      }
      try {
        const next = await getPhotoJobs()
        if (stop) return
        setJobs(next)
        busy =
          next.picker.state === 'waiting' ||
          next.picker.state === 'importing' ||
          next.drive.state === 'syncing'
        // Anything that finished while we were watching changed the pool.
        const signature = next.picker.state + next.drive.state
        const settled = next.picker.state === 'done' || next.drive.state === 'done'
        if (settled && previous !== null && previous !== signature) {
          refresh().catch(() => {})
        }
        previous = signature
      } catch {
        /* transient: keep polling, at the idle rate */
      }
      if (!stop) timer = setTimeout(tick, busy ? JOB_POLL_BUSY_MS : JOB_POLL_IDLE_MS)
    }

    // Nothing can change while the page is hidden that we need to know about
    // sooner than the next time it is looked at.
    const onVisible = () => {
      if (document.visibilityState === 'visible' && !stop) {
        clearTimeout(timer)
        tick()
      }
    }
    document.addEventListener('visibilitychange', onVisible)

    tick()
    return () => {
      stop = true
      clearTimeout(timer)
      document.removeEventListener('visibilitychange', onVisible)
    }
  }, [refresh])

  const run = async (label: string, action: () => Promise<string | null>) => {
    setBusy(label)
    setMessage(null)
    try {
      const note = await action()
      if (note) setMessage(note)
      await refresh()
    } catch (err) {
      setMessage(String(err instanceof Error ? err.message : err))
    } finally {
      setBusy(null)
    }
  }

  // --- sign in --------------------------------------------------------------

  const connect = (service: string) =>
    run('connect', async () => {
      const body = await postGoogleConnect(service)
      if (body.error || !body.auth_url) return body.error ?? 'Could not start sign-in.'
      setPending({ state: body.state ?? '' })
      window.open(body.auth_url, '_blank', 'noopener')
      return null
    })

  const completeSignIn = () =>
    run('complete', async () => {
      const body = await postGoogleComplete(pending?.state ?? '', pasted)
      if (!body.connected) return body.error ?? 'Sign-in did not complete.'
      setPending(null)
      setPasted('')
      return `Connected${body.account ? ` as ${body.account}` : ''}.`
    })

  const disconnect = (service: string) =>
    run('disconnect', async () => {
      await deleteGoogle(service)
      return 'Disconnected. Photos already in the pool stay put.'
    })

  // --- picking --------------------------------------------------------------

  const choosePhotos = () =>
    run('picker', async () => {
      const body = await postPickerSession()
      if (body.error || !body.picker_uri) return body.error ?? 'Could not open the picker.'
      window.open(body.picker_uri, '_blank', 'noopener')
      setJobs((j) => (j ? { ...j, picker: { state: 'waiting', picker_uri: body.picker_uri } } : j))
      return null
    })

  const stopPicking = () =>
    run('cancel', async () => {
      await postPickerCancel()
      return null
    })

  // --- drive ----------------------------------------------------------------

  const syncDrive = () =>
    run('drive', async () => {
      const result = await postDriveSync()
      if (result.error) return result.error
      return `Drive: ${result.added} new, ${result.found} in the folder.`
    })

  // --- pool -----------------------------------------------------------------

  const upload = (files: FileList | null) => {
    if (!files || files.length === 0) return
    return run('upload', async () => {
      const result = await uploadPhotos(Array.from(files))
      const dupes = result.duplicates ? `, ${result.duplicates} already in the pool` : ''
      if (result.errors.length) return `Added ${result.added}; skipped: ${result.errors.join('; ')}`
      return `Added ${result.added} photo${result.added === 1 ? '' : 's'}${dupes}.`
    })
  }

  const remove = (id: string) =>
    run('delete', async () => {
      await deletePhoto(id)
      return null
    })

  // Framing is stored per photo, next to the image rather than in the config —
  // it belongs to the picture, not to this draft, so it saves straight away.
  const saveFocus = async (id: string, focus: [number, number] | null) => {
    await putPhotoFocus(id, focus)
    setFraming(null)
    await refresh()
  }

  const removeAll = () => {
    if (!window.confirm('Remove every photo from the pool?')) return
    return run('clear', async () => {
      await clearPhotos()
      return 'Pool cleared.'
    })
  }

  const picker = jobs?.picker
  const drive = jobs?.drive
  const picking = picker?.state === 'waiting' || picker?.state === 'importing'
  const photosConnected = Boolean(google.photos?.connected)
  const driveConnected = Boolean(google.drive?.connected)
  const folder = (values.drive_folder as string) ?? ''
  const fit = (values.fit as 'cover' | 'contain') ?? 'cover'

  return (
    <div className="photos-panel">
      {hasCredentials && project && (
        <p className="muted">
          Signing in through Google Cloud project <code>{project}</code>. The APIs must be
          enabled <em>on that project</em> — it's whichever one{' '}
          <code>data/google_credentials.json</code> came from. Replace that file to switch
          projects (which signs you out of everything here).
        </p>
      )}
      {!hasCredentials && (
        <p className="muted">
          No OAuth client yet. Create one (type “Desktop app”) in the Google Cloud Console with
          the <em>Photos Picker API</em> and/or <em>Drive API</em> enabled, and save the client
          secret JSON as <code>data/google_credentials.json</code> on the device.
        </p>
      )}

      {/* ---- Google Photos ---- */}
      <div className="connect-card">
        <div className="row connect-row">
          <span className={`dot ${connectionState(google.photos)}`} />
          <strong>Google Photos</strong>
          <span className="muted">{connectionLabel(google.photos)}</span>
          <span className="spacer" />
          <button
            type="button"
            onClick={() => connect('photos')}
            disabled={!hasCredentials || busy !== null}
          >
            {photosConnected ? 'Reconnect' : 'Connect'}
          </button>
          {photosConnected && (
            <button type="button" onClick={() => disconnect('photos')} disabled={busy !== null}>
              Disconnect
            </button>
          )}
        </div>

        <div className="row">
          <button
            type="button"
            onClick={choosePhotos}
            disabled={!photosConnected || picking || busy !== null}
          >
            Choose photos…
          </button>
          {picking && (
            <button type="button" onClick={stopPicking} disabled={busy !== null}>
              Stop waiting
            </button>
          )}
        </div>

        {picking && (
          <p className="muted">
            {picker?.state === 'importing'
              ? 'Downloading your selection…'
              : 'Waiting for you to finish picking.'}{' '}
            Google shows recent photos rather than albums — <strong>search the album’s name</strong>{' '}
            to select its contents.{' '}
            {picker?.picker_uri && (
              <a href={picker.picker_uri} target="_blank" rel="noreferrer">
                Open the picker
              </a>
            )}
            . You can close this page: the download happens on the device.
          </p>
        )}
        {picker && !picking && <p className="muted">{pickerSummary(picker)}</p>}
      </div>

      {/* ---- Google Drive ---- */}
      <div className="connect-card">
        <div className="row connect-row">
          <span className={`dot ${connectionState(google.drive)}`} />
          <strong>Google Drive folder</strong>
          <span className="muted">{connectionLabel(google.drive)}</span>
          <span className="spacer" />
          <button
            type="button"
            onClick={() => connect('drive')}
            disabled={!hasCredentials || busy !== null}
          >
            {driveConnected ? 'Reconnect' : 'Connect'}
          </button>
          {driveConnected && (
            <button type="button" onClick={() => disconnect('drive')} disabled={busy !== null}>
              Disconnect
            </button>
          )}
        </div>

        <p className="muted">
          The frame checks this folder on a schedule, so photos you drop in later appear on their
          own. Google offers no way to watch a folder without granting{' '}
          <strong>read access to your whole Drive</strong> — the narrow per-file permission can’t
          see inside a folder. Skip this section if you’d rather not.
        </p>

        <label>
          Folder link or id
          <input
            value={folder}
            placeholder="https://drive.google.com/drive/folders/…"
            onChange={(e) => onPatch({ drive_folder: e.target.value })}
            autoComplete="off"
            data-1p-ignore
            data-lpignore="true"
          />
        </label>

        <div className="row">
          <button
            type="button"
            onClick={syncDrive}
            disabled={!driveConnected || !folder || drive?.state === 'syncing' || busy !== null}
          >
            {drive?.state === 'syncing' ? 'Syncing…' : 'Sync now'}
          </button>
          <span className="muted">{driveSummary(drive)}</span>
        </div>
        {folder && <p className="muted">Save your changes before syncing a folder you just set.</p>}
      </div>

      {/* ---- sign-in paste bridge ---- */}
      {pending && (
        <div className="paste-bridge connect-card">
          <p className="muted">
            Finish signing in on the tab that just opened. Google then sends your browser to{' '}
            <code>http://localhost:8000/</code> — an address on the machine you're browsing from,
            not this device. Copy that whole address from the address bar and paste it here.
            (If a copy of this app answers there, it will tell you the sign-in belongs elsewhere
            and offer the address to copy.)
          </p>
          <div className="row">
            <input
              value={pasted}
              placeholder="http://localhost:8000/?state=…&code=…"
              onChange={(e) => setPasted(e.target.value)}
              autoComplete="off"
              data-1p-ignore
              data-lpignore="true"
            />
            <button
              type="button"
              className="primary"
              onClick={completeSignIn}
              disabled={!pasted.trim() || busy !== null}
            >
              Finish
            </button>
            <button type="button" onClick={() => setPending(null)} disabled={busy !== null}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ---- the pool ---- */}
      <div className="row photo-actions">
        <label className="upload-button">
          Upload images
          <input
            type="file"
            accept="image/*"
            multiple
            disabled={busy !== null}
            onChange={(e) => {
              upload(e.target.files)
              e.target.value = ''
            }}
          />
        </label>
        {pool && pool.photos.length > 0 && (
          <button type="button" onClick={removeAll} disabled={busy !== null}>
            Clear all
          </button>
        )}
      </div>

      {message && <p className="muted">{message}</p>}

      {pool && (
        <>
          <p className="muted">
            {pool.photos.length} of {pool.max_photos} photos · {formatBytes(pool.bytes_used)} on
            disk. The oldest are dropped once the pool is full.
            {fit === 'cover' && ' Click a photo to see it on the display and position the crop.'}
          </p>
          <div className="photo-grid">
            {pool.photos.map((photo) => (
              <figure key={photo.id} className="photo-thumb">
                <button
                  type="button"
                  className="photo-open"
                  title={`Position ${photo.filename || 'this photo'} on the display`}
                  onClick={() => setFraming(photo)}
                >
                  <img
                    src={`/api/photos/thumb/${photo.id}`}
                    alt={photo.filename || 'photo'}
                    style={{ objectPosition: objectPosition(photo) }}
                  />
                </button>
                {!isCentred(focusOf(photo)) && (
                  <span className="photo-badge" title="Positioned by hand">
                    ⌖
                  </span>
                )}
                <button
                  type="button"
                  className="photo-remove"
                  title="Remove this photo"
                  onClick={() => remove(photo.id)}
                  disabled={busy !== null}
                >
                  ×
                </button>
              </figure>
            ))}
          </div>
          {pool.photos.length === 0 && (
            <p className="muted">
              The pool is empty — upload some images, pick from Google Photos, or point it at a
              Drive folder.
            </p>
          )}
        </>
      )}

      {framing && pool && (
        <PhotoFramer
          photo={framing}
          frame={pool.frame}
          fit={fit}
          onSave={(focus) => saveFocus(framing.id, focus)}
          onClose={() => setFraming(null)}
        />
      )}
    </div>
  )
}

/** Show each thumbnail cropped the way the panel will crop it, so the grid
 * already reflects a position rather than hiding it behind a click. */
function objectPosition(photo: PhotoEntry): string {
  const [x, y] = focusOf(photo)
  return `${x * 100}% ${y * 100}%`
}

function connectionState(status: GoogleServiceStatus | undefined): string {
  if (status?.connected) return 'ok'
  return status?.expired ? 'err' : 'pending'
}

function connectionLabel(status: GoogleServiceStatus | undefined): string {
  if (status?.connected) return status.account ?? 'connected'
  return status?.expired ? 'access expired — reconnect' : 'not connected'
}

function pickerSummary(picker: PhotoJobs['picker']): string {
  switch (picker.state) {
    case 'done': {
      const dupes = picker.duplicates ? `, ${picker.duplicates} already had` : ''
      const skipped = picker.skipped ? `, ${picker.skipped} skipped` : ''
      return `Last pick: ${picker.imported} imported${dupes}${skipped}.`
    }
    case 'error':
      return `Last pick failed: ${picker.error}`
    case 'timeout':
      return 'The last picking session timed out before anything was chosen.'
    case 'cancelled':
      return 'Picking cancelled.'
    default:
      return ''
  }
}

function driveSummary(drive: PhotoJobs['drive'] | undefined): string {
  if (!drive) return ''
  if (drive.state === 'error') return `Sync failed: ${drive.error}`
  if (drive.state === 'done') {
    return `Last sync: ${drive.added} new of ${drive.found} in the folder.`
  }
  return ''
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} kB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
