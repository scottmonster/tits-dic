import type { PageServerLoad } from './$types';

import { loadOverviewData } from '$lib/server/derived-data';

export const load: PageServerLoad = async () => {
	return await loadOverviewData();
};