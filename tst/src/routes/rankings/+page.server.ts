import type { PageServerLoad } from './$types';

import { loadRankingsData } from '$lib/server/derived-data';

export const load: PageServerLoad = async ({ url }) => {
	return await loadRankingsData(url.searchParams.get('metric'), url.searchParams.get('input'));
};