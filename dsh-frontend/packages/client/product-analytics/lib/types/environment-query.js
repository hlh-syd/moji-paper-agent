/** Bounded, cancellable queries of fixed operating-system metadata commands. */
import { execFile } from 'node:child_process';
/**
 * Execute fixed argv without a shell; failures omit only dependent metadata.
 * @param command - operating-system executable, never event-supplied text.
 * @param args - fixed metadata query arguments.
 * @param options - output/time limits and owner lifetime.
 * @returns stdout or a fixed failure reason, after the child closes.
 */
export async function queryEnvironment(command, args, options) {
    const result = Promise.withResolvers();
    let child;
    try {
        child = execFile(command, [...args], {
            encoding: 'utf8', windowsHide: true, signal: options.signal, killSignal: 'SIGKILL',
            timeout: options.timeoutMillis, maxBuffer: options.maxOutputBytes,
        }, (error, stdout) => {
            if (error === null) {
                result.resolve({ kind: 'value', stdout });
                return;
            }
            const reason = error.code === 'ERR_CHILD_PROCESS_STDIO_MAXBUFFER' ? 'output-limit'
                : error.killed && !options.signal.aborted ? 'timeout' : 'query-failed';
            result.resolve({ kind: 'missing', reason });
        });
    }
    catch (_error) {
        // Synchronous OS spawn failures create no child; other queries remain owned.
        return { kind: 'missing', reason: 'query-failed' };
    }
    // execFile can report abort before stdio closes; disposal owns both settlements.
    const closed = new Promise((resolve) => { child.once('close', () => { resolve(); }); });
    const [output] = await Promise.all([result.promise, closed]);
    return output;
}
//# sourceMappingURL=environment-query.js.map