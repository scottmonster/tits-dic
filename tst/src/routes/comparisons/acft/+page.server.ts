import type { PageServerLoad } from './$types';

import { loadAcftComparisonsData } from '$lib/server/derived-data';

export const load: PageServerLoad = async () => {
	return await loadAcftComparisonsData();
};