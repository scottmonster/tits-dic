import type { PageServerLoad } from './$types';

import { loadAcftInsightsData } from '$lib/server/derived-data';

export const load: PageServerLoad = async () => {
	return await loadAcftInsightsData();
};