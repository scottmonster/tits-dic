export function modelHref(model: string): string {
	return `/models/${encodeURIComponent(model)}`;
}