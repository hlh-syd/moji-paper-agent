/**
 * @param expiresAt - server expiration timestamp.
 * @param at - milliseconds since epoch.
 * @returns whether the bonus can no longer be shown.
 */
function expired(expiresAt, at) {
    if (expiresAt === '')
        return false;
    const expires = Date.parse(expiresAt);
    // An unparsable timestamp is not evidence of expiry, so the notice still shows.
    return !Number.isNaN(expires) && expires <= at;
}
/**
 * Own the read, display, and acknowledgement lifecycle of bonus notices.
 * @param options - collaborators and timings.
 * @returns the controller the account plugin drives from account and settings state.
 */
export function createBonusNoticeController(options) {
    let scope;
    /** Notice currently published to the sidebar, including the expiration checked at display time. */
    let current = [];
    /** Newest read; an older read still in flight has its result dropped once a newer one starts. */
    let readGeneration = 0;
    let ackTimer;
    let ackInFlight = false;
    let ackDelay = options.ackRetryDelayMs;
    /** Orders the card on screen already reported, so one mount cannot acknowledge twice. */
    const reported = new Set();
    const clearAckTimer = () => { if (ackTimer !== undefined) {
        clearTimeout(ackTimer);
        ackTimer = undefined;
    } };
    const setCurrent = (notices) => {
        if (notices.length === current.length && notices.every((notice, index) => {
            const previous = current[index];
            return notice.orderId === previous?.orderId && notice.message === previous.message
                && notice.title === previous.title && notice.expiresAt === previous.expiresAt;
        }))
            return;
        const retained = new Set(notices.map(notice => notice.orderId));
        for (const orderId of reported)
            if (!retained.has(orderId))
                reported.delete(orderId);
        current = notices;
        options.publish(notices);
    };
    const scheduleAck = (delay) => {
        clearAckTimer();
        ackTimer = setTimeout(() => { void runAcks(); }, delay);
    };
    /** Drop acknowledgement work of the previous account or lifecycle, so its carry-over cannot reach the next one. */
    const resetAcks = () => {
        clearAckTimer();
        ackInFlight = false;
        ackDelay = options.ackRetryDelayMs;
    };
    const startAcks = () => { if (scope !== undefined && scope.pending.length > 0)
        scheduleAck(0); };
    /**
     * Apply the server-ordered stack, excluding expired notices. An empty batch
     * retains only the foreground card if the user has already seen it.
     */
    const apply = (scopeAtRead, batch) => {
        if (scopeAtRead.accountId !== batch.accountId) {
            // A different account answered: the previous account's card and pending
            // acknowledgements belong to it and must not outlive its sign-in.
            scopeAtRead.accountId = batch.accountId;
            scopeAtRead.pending.length = 0;
            resetAcks();
            setCurrent([]);
        }
        const candidates = batch.bonuses.filter(notice => !expired(notice.expiresAt, Date.now()));
        if (candidates.length === 0) {
            // Keep an already presented foreground card until dismissal; unseen cards are withdrawn.
            const foreground = current[0];
            setCurrent(foreground !== undefined && reported.has(foreground.orderId) ? [foreground] : []);
            return;
        }
        setCurrent(candidates);
    };
    /**
     * Read once for one live lifecycle. A newer read supersedes one in flight: the
     * older result is dropped instead of applied, so only the newest read can report
     * an account change.
     * @param scopeAtRead - lifecycle this read belongs to.
     * @returns after this read settles.
     */
    function runRead(scopeAtRead) {
        const generation = ++readGeneration;
        return (async () => {
            let batch;
            try {
                batch = await options.read();
            }
            catch {
                // A failed read reports nothing to the user and leaves the lifecycle as
                // it stands, so a pending acknowledgement keeps retrying.
                batch = null;
            }
            if (scope !== scopeAtRead || generation !== readGeneration)
                return;
            if (batch !== null)
                apply(scopeAtRead, batch);
        })();
    }
    /**
     * Acknowledge presented orders one at a time. A protocol failure backs off up
     * to the ceiling and retries for the rest of the signed-in lifecycle, which
     * never persists. A false answer settles the order without a retry: this
     * session cannot make the server accept it. A hidden window keeps retrying,
     * because the presented frame is what a pending acknowledgement proves.
     */
    async function runAcks() {
        const scopeAtCall = scope;
        if (scopeAtCall === undefined || ackInFlight)
            return;
        const accountId = scopeAtCall.accountId;
        const orderId = scopeAtCall.pending[0];
        /* v8 ignore next -- an acknowledgement runs only while a read named the account and left a pending order */
        if (accountId === undefined || orderId === undefined)
            return;
        ackInFlight = true;
        /** @returns whether this call still owns the lifecycle and account it started for. */
        const owns = () => scope === scopeAtCall && scopeAtCall.accountId === accountId;
        let acknowledged;
        try {
            acknowledged = await options.acknowledge(accountId, orderId);
        }
        catch {
            // The lifecycle ended or another account answered while the call was in flight:
            // its pending list, in-flight flag and timer now belong to the new owner.
            if (!owns())
                return;
            ackInFlight = false;
            scheduleAck(ackDelay);
            ackDelay = Math.min(ackDelay * 2, options.ackRetryMaxDelayMs);
            return;
        }
        if (!owns())
            return;
        ackInFlight = false;
        // Success needs no record and a refusal has no retry, so either way this order
        // is settled for this lifecycle.
        /* v8 ignore next -- the call owns the scope, and this order is its head until this shift */
        if (scopeAtCall.pending[0] === orderId)
            scopeAtCall.pending.shift();
        ackDelay = options.ackRetryDelayMs;
        if (acknowledged && scopeAtCall.pending.length > 0)
            scheduleAck(0);
        else
            clearAckTimer();
    }
    /** Queue a presented order, so an acknowledgement interrupted by a failure retries within this sign-in. */
    const report = (orderId) => {
        const scopeAtReport = scope;
        if (scopeAtReport === undefined || current[0]?.orderId !== orderId || reported.has(orderId))
            return;
        reported.add(orderId);
        if (!scopeAtReport.pending.includes(orderId))
            scopeAtReport.pending.push(orderId);
        ackDelay = options.ackRetryDelayMs;
        scheduleAck(0);
    };
    const endLifecycle = () => {
        if (scope === undefined)
            return;
        // The lifecycle a read started for is gone, so its scope check drops the result.
        resetAcks();
        scope = undefined;
        reported.clear();
        setCurrent([]);
    };
    return {
        begin() {
            // Re-entry is the same signed-in session: the card and its acknowledgement
            // retries stay, and this frame's own read supersedes any read still in flight,
            // so a credential swapped in place is never left unnamed.
            scope ??= { pending: [] };
            void runRead(scope);
        },
        end: endLifecycle,
        refresh() {
            if (scope === undefined)
                return Promise.resolve();
            startAcks();
            return runRead(scope);
        },
        shown(orderId) {
            setCurrent(current.filter(notice => !expired(notice.expiresAt, Date.now())));
            if (current[0]?.orderId !== orderId)
                return;
            report(orderId);
        },
        dismiss(orderId) {
            setCurrent(current.filter(notice => !expired(notice.expiresAt, Date.now())));
            if (current[0]?.orderId !== orderId)
                return;
            // Closing the card is the user seeing it, so queue its acknowledgement before withdrawing.
            report(orderId);
            setCurrent(current.slice(1).filter(notice => !expired(notice.expiresAt, Date.now())));
        },
    };
}
//# sourceMappingURL=bonus-notices.js.map