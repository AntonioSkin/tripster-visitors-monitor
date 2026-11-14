// Read-only CLI. It never accepts recovery options or calls mutation APIs.
import {GitHubClient, cycle} from './core.mjs';

const memory = new Map();
const storage = {get:async key => memory.get(key), put:async (key, value) => memory.set(key, value)};
const result = await cycle({client:new GitHubClient(process.env.GITHUB_TOKEN), storage, mode:'observe'});
console.log(JSON.stringify(result, null, 2));
if (result.tasks.some(t => t.status === 'unknown')) process.exitCode = 1;
