import { useEffect, useMemo, useRef, useState, type MouseEvent } from 'react'

import {
  describeError,
  getArticle,
  listReviewArticles,
  listRules,
  type ArticleDetail,
  type Observable,
  type PipelineRun,
  type RuleDetail,
} from './api'
import { groupByField, isActive, stageOf, useArticleRun } from './articleRun'
import { READER_VIEWS, VIEW_LABELS, type ReaderView } from './readerView'
import { GroupProblems, Observables, RelevanceVerdict, RunBanners } from './RunSections'
import { Meta } from './ArticlesPage'
import { evidenceAnchor, type Mark } from './highlight'
import { HighlightedText } from './HighlightedText'
import { PriorityBreakdown } from './PriorityBreakdown'
import { ReviewItem } from './ReviewItem'
import { followLink } from './routes'

const STEPS = ['junk_filter', 'relevance', 'extraction', 'rule_generation'] as const
type Step = (typeof STEPS)[number]
type StepState = 'not_run' | 'waiting' | 'running' | 'done' | 'failed' | 'not_reached'

function stepState(run: PipelineRun | null | undefined, step: Step): StepState {
  if (!run) return 'not_run'
  const stage = stageOf(run, step)
  if (stage) return stage.status === 'failed' ? 'failed' : 'done'
  if (run.status === 'queued') return 'waiting'
  if (run.status === 'running') {
    const current = STEPS.indexOf((run.current_step ?? 'junk_filter') as Step)
    const index = STEPS.indexOf(step)
    return index === current ? 'running' : index < current ? 'done' : 'waiting'
  }
  if (run.status === 'failed' && run.current_step === step) return 'failed'
  return 'not_reached'
}

const STEP_TEXT: Record<StepState, string> = {
  done: 'Done',
  not_run: 'Not run',
  waiting: 'Waiting',
  running: 'Running',
  failed: 'Failed',
  not_reached: 'Not reached',
}

/** Why a later stage never ran: the run stopped at an earlier one. */
function stoppedText(run: PipelineRun | null | undefined, state: StepState): string {
  if (state === 'not_reached' && run?.status === 'filtered_out') {
    return run.current_step === 'junk_filter' ? 'Skipped by the junk filter' : 'Stopped at relevance'
  }
  return STEP_TEXT[state]
}

function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`
}

/** One line of outcome per stage, as text, for the stage strip. */
function stageOutcomes(article: ArticleDetail, run: PipelineRun | null | undefined, rules: RuleDetail[] | undefined) {
  const priority = article.priority ? `Priority ${article.priority.score.toFixed(1)} · ${article.priority.band}` : 'Not scored'

  let relevance: string
  const verdict = stageOf(run, 'relevance')
  if (run?.status === 'filtered_out' && run.current_step === 'junk_filter') relevance = 'Junk filter: skipped'
  else if (verdict && verdict.status !== 'failed') {
    const output = verdict.output as { score: number; min_score: number }
    relevance = `${verdict.status === 'passed' ? 'Relevant' : 'Not relevant'} ${output.score}/10 (needs ${output.min_score})`
  } else relevance = `Relevance: ${STEP_TEXT[stepState(run, 'relevance')].toLowerCase()}`

  const extractionState = stepState(run, 'extraction')
  const extraction =
    extractionState === 'done' && run
      ? run.observables.length === 0
        ? 'No observables'
        : `${plural(run.observables.length, 'observable')} · ${run.observables.filter((item) => !item.evidence_verified).length} unverified`
      : stoppedText(run, extractionState)

  const rulesState = stepState(run, 'rule_generation')
  const pending = rules?.filter((rule) => rule.status === 'pending_review').length ?? 0
  const ruleText =
    rules && rules.length > 0
      ? `${plural(rules.length, 'rule')} · ${pending} pending`
      : rulesState === 'done'
        ? 'No rules'
        : stoppedText(run, rulesState)

  return { filtering: [priority, relevance], extraction: [extraction], rules: [ruleText] }
}

const RUN_STATUS: Record<PipelineRun['status'], string> = {
  queued: 'queued',
  running: 'running',
  filtered_out: 'filtered out',
  succeeded: 'succeeded',
  failed: 'failed',
}
const DATE_FORMAT = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })

function evidenceOf(run: PipelineRun | null | undefined): Mark[] {
  return (run?.observables ?? []).flatMap((item) =>
    item.evidence_span
      ? [{ start: item.evidence_span.start, end: item.evidence_span.end, kind: 'evidence' as const, label: item.field_name }]
      : [],
  )
}

function sameMarks(a: Mark[], b: Mark[]): boolean {
  return a.length === b.length && a.every((mark, index) => mark.start === b[index].start && mark.end === b[index].end && mark.label === b[index].label)
}

type Highlights = Record<'filtering' | 'extraction', { keywords: boolean; evidence: boolean }>

/**
 * One article and everything the pipeline did with it: why it was picked (Filtering), what was extracted
 * (Extraction), and the rules it produced, reviewed in place (Rules). The view is kept in the URL (`?view=`).
 */
export function ArticleReader({
  id,
  view,
  onViewChange = () => {},
  backLabel = 'All articles',
  onBack,
  onOpenArticle = () => {},
  onOpenNext,
  onRulesChanged = () => {},
  pollMs,
}: {
  id: number
  view?: ReaderView // from the URL; undefined picks Rules when a rule is pending, else Filtering
  onViewChange?: (view: ReaderView) => void
  backLabel?: string
  onBack: () => void
  onOpenArticle?: (id: number) => void
  onOpenNext?: (id: number) => void // set when the reviewer came from the review queue: offers the next article
  onRulesChanged?: () => void // e.g. so the sidebar's pending count follows decisions made here
  pollMs?: number
}) {
  const [article, setArticle] = useState<ArticleDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const runState = useArticleRun(id, pollMs)
  const { run, active } = runState

  useEffect(() => {
    getArticle(id)
      .then(setArticle)
      .catch((failure) => setError(describeError(failure)))
  }, [id])

  // Rules from every run of this article (an earlier run may still have rules waiting), reloaded when a run finishes.
  const [rules, setRules] = useState<RuleDetail[] | undefined>(undefined)
  const [rulesError, setRulesError] = useState<string | null>(null)
  const [rulesVersion, setRulesVersion] = useState(0)
  const [wasActive, setWasActive] = useState(active)
  if (wasActive !== active) {
    setWasActive(active)
    if (!active) setRulesVersion((version) => version + 1)
  }
  // Decisions made on this page; a list requested before the latest one may hold its old status.
  const decisions = useRef(0)
  useEffect(() => {
    let current = true
    const startedAfter = decisions.current
    listRules({ articleId: id })
      .then((result) => {
        if (!current) return
        if (decisions.current !== startedAfter) {
          setRulesVersion((version) => version + 1) // ask again rather than undo the decision on screen
          return
        }
        setRules(result)
        setRulesError(null)
      })
      .catch((failure) => {
        if (!current) return
        setRules((previous) => previous ?? [])
        setRulesError(`Could not load rules: ${describeError(failure)}`)
      })
    return () => {
      current = false
    }
  }, [id, rulesVersion])

  const [chosen, setChosen] = useState<ReaderView | undefined>(view)
  const [urlView, setUrlView] = useState(view)
  if (urlView !== view) {
    // browser back and forward
    setUrlView(view)
    setChosen(view)
  }
  const shown: ReaderView | undefined =
    chosen ?? (rules === undefined ? undefined : rules.some((rule) => rule.status === 'pending_review') ? 'rules' : 'filtering')
  // The default is picked once: deciding the last pending rule, or a run finishing, must not move the reviewer.
  if (chosen === undefined && shown !== undefined) setChosen(shown)

  const viewHeading = useRef<HTMLHeadingElement>(null)
  const focusView = useRef(false)
  useEffect(() => {
    if (focusView.current) {
      focusView.current = false
      viewHeading.current?.focus()
    }
  }, [shown])

  function select(event: MouseEvent<HTMLAnchorElement>, next: ReaderView) {
    followLink(event, () => {
      if (next === shown) {
        viewHeading.current?.focus() // already here: no new history entry
        return
      }
      focusView.current = true
      pendingTarget.current = null
      setChosen(next)
      onViewChange(next)
    })
  }

  const [highlights, setHighlights] = useState<Highlights>({
    filtering: { keywords: true, evidence: false },
    extraction: { keywords: false, evidence: true },
  })
  const keywordMarks = useMemo<Mark[]>(
    () =>
      (article?.priority?.spans ?? []).map((span) => ({
        start: span.start,
        end: span.end,
        kind: 'keyword',
        label: span.term,
        bucket: span.bucket,
      })),
    [article],
  )
  // Keep the same array while polling returns the same evidence, so the (memoised) text doesn't re-split.
  const [evidenceMarks, setEvidenceMarks] = useState<Mark[]>([])
  const latestEvidence = evidenceOf(run)
  if (!sameMarks(evidenceMarks, latestEvidence)) setEvidenceMarks(latestEvidence)
  const textView = shown === 'extraction' ? 'extraction' : 'filtering'
  const toggles = highlights[textView]
  const marks = useMemo(
    () => [...(toggles.keywords ? keywordMarks : []), ...(toggles.evidence ? evidenceMarks : [])],
    [toggles, keywordMarks, evidenceMarks],
  )
  function toggle(kind: 'keywords' | 'evidence', on: boolean) {
    setHighlights((current) => ({ ...current, [textView]: { ...current[textView], [kind]: on } }))
  }

  // "Show in text" and "Built from N observables" move to an element that may only exist after the next render.
  const pendingTarget = useRef<string | null>(null)
  function goTo(id: string) {
    const target = document.getElementById(id)
    if (target) reveal(target)
    else pendingTarget.current = id
  }
  function showInText(item: Observable) {
    if (!item.evidence_span) return
    if (!highlights.extraction.evidence) setHighlights((current) => ({ ...current, extraction: { ...current.extraction, evidence: true } }))
    goTo(evidenceAnchor(item.evidence_span.start))
  }

  // The observables a rule was built from, marked in the Extraction list until another rule's are shown.
  const [focusedObservables, setFocusedObservables] = useState<ReadonlySet<number>>(new Set())
  function showObservables(ids: number[]) {
    const listed = [...groupByField(run?.observables ?? []).values()].flat()
    const first = listed.find((item) => ids.includes(item.id))
    setFocusedObservables(new Set(ids))
    setChosen('extraction')
    onViewChange('extraction')
    if (first) pendingTarget.current = `observable-${first.id}`
    else focusView.current = true // nothing to point at: land on the view, not on <body>
  }
  // After a render, move to the element a jump asked for once it exists.
  useEffect(() => {
    const target = pendingTarget.current && document.getElementById(pendingTarget.current)
    if (!target) return
    pendingTarget.current = null
    reveal(target)
  })

  const outcomes = article ? stageOutcomes(article, run, rules) : null

  return (
    <article className="page reader">
      <button type="button" className="link-button back" onClick={onBack}>
        ← {backLabel}
      </button>
      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {!article && !error && <p className="muted">Loading article…</p>}
      {article && outcomes && (
        <>
          <header className="reader-header">
            <div className="reader-title">
              <h1>{article.title}</h1>
              <Meta article={article} />
              <p>
                <a href={article.url} target="_blank" rel="noreferrer">
                  Open original ↗
                </a>
              </p>
            </div>
            <div className="run-line">
              <p className="muted small" role="status">
                {run === undefined
                  ? 'Loading runs…'
                  : run === null
                    ? 'Not run yet.'
                    : run.status === 'running' && !runState.workerGone
                      ? `Run #${run.id} running: ${run.current_step ?? 'starting'}…`
                      : `Run #${run.id} · ${RUN_STATUS[run.status]} ${DATE_FORMAT.format(new Date(run.finished_at ?? run.created_at))} · ${run.profile.slug} v${run.profile.version}`}
              </p>
              <button type="button" onClick={runState.start} disabled={runState.queueing || active || run === undefined}>
                {run && !active ? 'Run again' : 'Run detection'}
              </button>
            </div>
          </header>

          <RunBanners state={runState} />

          <nav className="stage-strip" aria-label="Pipeline stages">
            {READER_VIEWS.map((candidate) => (
              <a
                key={candidate}
                href={`/articles/${id}?view=${candidate}`}
                className={candidate === shown ? 'stage is-selected' : 'stage'}
                aria-current={candidate === shown ? 'page' : undefined}
                onClick={(event) => select(event, candidate)}
              >
                <span className="stage-name">{VIEW_LABELS[candidate]}</span>
                {outcomes[candidate].map((line) => (
                  <span key={line} className="stage-outcome">
                    {line}
                  </span>
                ))}
              </a>
            ))}
          </nav>

          {shown === undefined && <p className="muted">Loading rules…</p>}
          {shown !== undefined && (
            <h2 className="visually-hidden" tabIndex={-1} ref={viewHeading}>
              {VIEW_LABELS[shown]}
            </h2>
          )}

          {(shown === 'filtering' || shown === 'extraction') && (
            <div className="reader-layout">
              <div className="reader-main">
                <fieldset className="highlight-toggles">
                  <legend>Highlight</legend>
                  <label>
                    <input type="checkbox" checked={toggles.keywords} onChange={(event) => toggle('keywords', event.target.checked)} />{' '}
                    Keywords ({keywordMarks.length.toLocaleString('en-US')})
                  </label>
                  <label>
                    <input type="checkbox" checked={toggles.evidence} onChange={(event) => toggle('evidence', event.target.checked)} />{' '}
                    Evidence ({evidenceMarks.length})
                  </label>
                  <ul className="highlight-legend" aria-label="Highlight styles">
                    <li><mark className="hl hl-perfect">perfect</mark></li>
                    <li><mark className="hl hl-lolbas">lolbas</mark></li>
                    <li><mark className="hl hl-intelligence">intelligence</mark></li>
                    <li><mark className="hl hl-good">good</mark></li>
                    <li><mark className="hl hl-negative">negative</mark></li>
                    <li><mark className="hl hl-evidence">evidence quote</mark></li>
                  </ul>
                </fieldset>
                <HighlightedText text={article.content_text} marks={marks} />
              </div>
              <aside className="reader-side" aria-label={shown === 'filtering' ? 'Filtering' : 'Extraction'}>
                {shown === 'filtering' ? (
                  <>
                    {article.priority ? (
                      <PriorityBreakdown priority={article.priority} />
                    ) : (
                      <section className="panel">
                        <h2>Priority score</h2>
                        <p className="muted">Not scored for the current profile yet.</p>
                      </section>
                    )}
                    <RelevanceVerdict run={run} />
                  </>
                ) : (
                  <section className="panel" aria-labelledby="observables-heading">
                    <h2 id="observables-heading">Observables</h2>
                    <Observables
                      run={run}
                      notReached={notExtracted(run)}
                      focused={focusedObservables}
                      onShowInText={showInText}
                    />
                  </section>
                )}
              </aside>
            </div>
          )}

          {shown === 'rules' && (
            <RulesView
              onShowObservables={showObservables}
              articleId={id}
              onOpenNext={onOpenNext}
              run={run}
              rules={rules}
              error={rulesError}
              onOpenArticle={onOpenArticle}
              onUpdated={(updated) => {
                onRulesChanged()
                decisions.current += 1
                setRules((current) => current?.map((rule) => (rule.id === updated.id ? updated : rule)))
              }}
            />
          )}
        </>
      )}
    </article>
  )
}

function notExtracted(run: PipelineRun | null | undefined): string {
  const state = stepState(run, 'extraction')
  if (state === 'not_run') return 'Not run yet. Run detection to extract observables.'
  if (state === 'waiting' || state === 'running') return 'Observables appear here when extraction finishes.'
  if (state === 'failed') return 'Extraction failed (see the error above).'
  return `Nothing was extracted: ${stoppedText(run, state).toLowerCase()}.`
}

function RulesView({
  onShowObservables,
  articleId,
  onOpenNext,
  run,
  rules,
  error,
  onUpdated,
  onOpenArticle,
}: {
  onShowObservables: (ids: number[]) => void
  articleId: number
  onOpenNext?: (id: number) => void
  run: PipelineRun | null | undefined
  rules: RuleDetail[] | undefined
  error: string | null
  onUpdated: (rule: RuleDetail) => void
  onOpenArticle: (id: number) => void
}) {
  // After a decision, move focus to the next rule still waiting (or the "all decided" note): the buttons just used
  // may have changed or gone.
  const focusNext = useRef(false)
  const decided = useRef<HTMLParagraphElement>(null)
  const pending = rules?.filter((rule) => rule.status === 'pending_review') ?? []
  useEffect(() => {
    if (!focusNext.current) return
    focusNext.current = false
    if (pending.length > 0) document.getElementById(`rule-${pending[0].id}`)?.focus()
    else decided.current?.focus()
  })

  function updated(rule: RuleDetail, before: RuleDetail['status']) {
    if (before === 'pending_review' && rule.status !== 'pending_review') focusNext.current = true
    onUpdated(rule)
  }

  const generation = stageOf(run, 'rule_generation')
  const note = generation?.output.note as string | undefined
  const latest = rules?.filter((rule) => rule.run_id === run?.id) ?? []
  const running = isActive(run)
  const earlierRuns = [...new Set((rules ?? []).filter((rule) => rule.run_id !== run?.id).map((rule) => rule.run_id))]
  const approved = rules?.filter((rule) => rule.status === 'approved').length ?? 0

  // Only the latest run's observables are listed, so only its rules can point at their sources.
  function cards(list: RuleDetail[], latestRun = false) {
    return list.map((rule) => (
      <ReviewItem
        key={rule.id}
        rule={rule}
        showArticle={false}
        onOpenArticle={onOpenArticle}
        onUpdated={(next) => updated(next, rule.status)}
        onShowObservables={latestRun ? () => onShowObservables(rule.observable_ids) : undefined}
      />
    ))
  }

  return (
    <section className="rules-view" aria-label="Rules">
      {error && (
        <p className="banner banner-error" role="alert">
          {error}
        </p>
      )}
      {(rules === undefined || run === undefined) && <p className="muted">Loading rules…</p>}
      {rules && rules.length > 0 && pending.length === 0 && (
        <div className="decided-bar">
          <p className="banner banner-ok" role="status" tabIndex={-1} ref={decided}>
            All {plural(rules.length, 'rule')} decided ({approved} approved, {rules.length - approved} rejected).
          </p>
          {onOpenNext && <NextInQueue currentId={articleId} onOpen={onOpenNext} />}
        </div>
      )}
      {run !== undefined && rules && rules.length === 0 && !generation && !running && (
        <p className="muted">
          {run === null
            ? 'Not run yet. Run detection to generate rules.'
            : `No rules: ${stoppedText(run, stepState(run, 'rule_generation')).toLowerCase()}.`}
        </p>
      )}
      {run && (latest.length > 0 || generation || running) && (
        <div className="rules">
          <h3>Latest run (#{run.id})</h3>
          {running && <p className="muted">Rules appear here when the run finishes.</p>}
          {note && <p className="muted">{note}</p>}
          {cards(latest, true)}
          {generation && <GroupProblems output={generation.output} />}
        </div>
      )}
      {run !== undefined && earlierRuns.map((runId) => (
        <div key={runId} className="rules">
          <h3>{run ? `Earlier run (#${runId})` : `Run #${runId}`}</h3>
          {cards((rules ?? []).filter((rule) => rule.run_id === runId))}
        </div>
      ))}
    </section>
  )
}

/** Opens the highest-priority other article still waiting for review (the queue is read fresh, so it never goes stale). */
function NextInQueue({ currentId, onOpen }: { currentId: number; onOpen: (id: number) => void }) {
  const [state, setState] = useState<'idle' | 'loading' | 'empty' | string>('idle')

  async function next() {
    setState('loading')
    try {
      const queue = await listReviewArticles('pending_review')
      const target = queue.find((row) => row.article.id !== currentId)
      if (target) onOpen(target.article.id)
      else setState('empty')
    } catch (failure) {
      setState(`Could not load the review queue: ${describeError(failure)}`)
    }
  }

  return (
    <div className="next-in-queue">
      <button type="button" onClick={() => void next()} disabled={state === 'loading'}>
        Next article in queue →
      </button>
      {state === 'empty' && (
        <p className="muted" role="status">
          No other article is waiting for review.
        </p>
      )}
      {state !== 'idle' && state !== 'loading' && state !== 'empty' && (
        <p className="form-error" role="alert">
          {state}
        </p>
      )}
    </div>
  )
}

/** Scroll an element into view (instantly when the reader prefers reduced motion) and move focus to it. */
function reveal(element: HTMLElement) {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  element.scrollIntoView?.({ block: 'center', behavior: reduced ? 'auto' : 'smooth' })
  element.focus({ preventScroll: true })
}
