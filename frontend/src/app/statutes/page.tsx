'use client';

import { useEffect, useMemo, useState } from 'react';
import { BookOpenText, ExternalLink, Search, ShieldCheck } from 'lucide-react';
import { fetchStatutes, StatutoryCitation } from '@/lib/api';

const officialResources = [
  { title: 'Consumer Protection Act, 2019', category: 'Consumer rights', authority: 'India Code', url: 'https://www.indiacode.nic.in/handle/123456789/21423' },
  { title: 'National Cyber Crime Reporting Portal', category: 'Cyber financial fraud', authority: 'Indian Cyber Crime Coordination Centre', url: 'https://www.cybercrime.gov.in/' },
  { title: 'Bharatiya Nagarik Suraksha Sanhita, 2023', category: 'Police procedure', authority: 'India Code', url: 'https://www.indiacode.nic.in/handle/123456789/21419' },
  { title: 'Free legal aid and legal services', category: 'Professional help', authority: 'NALSA', url: 'https://nalsa.gov.in/' },
  { title: 'RTI Online', category: 'Central Government RTI', authority: 'Department of Personnel & Training', url: 'https://rtionline.gov.in/' },
  { title: 'Customer liability in unauthorised electronic transactions', category: 'Banking fraud', authority: 'Reserve Bank of India', url: 'https://www.rbi.org.in/commonman/Upload/English/Notification/PDFs/NOTI1506072017.PDF' },
];

export default function LegalResourcesPage() {
  const [statutes, setStatutes] = useState<Record<string, StatutoryCitation[]>>({});
  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('ALL');
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchStatutes()
      .then((data) => { if (active) setStatutes(data); })
      .catch(() => {
        if (active) setError('The curated corpus could not be loaded. The official links above remain available.');
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const items = useMemo(() => (
    Object.entries(statutes)
      .flatMap(([itemCategory, list]) => list.map((item) => ({ ...item, category: itemCategory })))
      .filter((item) => {
        const haystack = `${item.section} ${item.act} ${item.title} ${item.description}`.toLowerCase();
        return (category === 'ALL' || item.category === category) && haystack.includes(query.toLowerCase());
      })
  ), [statutes, query, category]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
      <header>
        <p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#2f755b]"><BookOpenText className="size-4" />Grounded guidance</p>
        <h1 className="mt-2 text-3xl font-bold tracking-[-0.04em] text-[#17231f]">Legal resources</h1>
        <p className="mt-2 max-w-2xl text-sm leading-6 text-[#6b7872]">Plain-language reference material linked to official sources. The case conversation remains the primary way to find your next step.</p>
      </header>

      <section className="mt-8">
        <h2 className="text-sm font-bold text-[#2b3a34]">Official starting points</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {officialResources.map((resource) => (
            <a key={resource.url} href={resource.url} target="_blank" rel="noreferrer" className="group rounded-2xl border border-[#dce5e0] bg-white p-4 transition hover:-translate-y-0.5 hover:border-[#9eb9aa] hover:shadow-md">
              <div className="flex items-start justify-between gap-3"><span className="rounded-full bg-[#edf4ef] px-2 py-1 text-[9px] font-bold text-[#3c7057]">{resource.category}</span><ExternalLink className="size-4 text-[#829088] group-hover:text-[#174e3b]" /></div>
              <h3 className="mt-3 text-sm font-bold leading-5 text-[#293931]">{resource.title}</h3>
              <p className="mt-1 text-[10px] text-[#7f8c85]">{resource.authority}</p>
            </a>
          ))}
        </div>
      </section>

      <section className="mt-10">
        <div className="flex flex-col justify-between gap-3 sm:flex-row sm:items-end">
          <div><h2 className="text-sm font-bold text-[#2b3a34]">Seeded legal corpus</h2><p className="mt-1 text-xs text-[#78857e]">A small MVP corpus used by the retrieval abstraction.</p></div>
          <div className="relative sm:w-80"><Search className="absolute left-3 top-2.5 size-4 text-[#87938d]" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search sections or topics" aria-label="Search legal resources" className="w-full rounded-xl border border-[#ccd8d1] bg-white py-2.5 pl-9 pr-3 text-xs focus:border-[#5d8c73] focus:outline-none" /></div>
        </div>
        <div className="mt-3 flex flex-wrap gap-2">
          {['ALL', 'CONSUMER', 'TENANCY', 'RTI'].map((item) => <button key={item} type="button" onClick={() => setCategory(item)} className={`rounded-full px-3 py-1.5 text-[10px] font-bold ${category === item ? 'bg-[#174e3b] text-white' : 'border border-[#d4ddd7] bg-white text-[#68766f]'}`}>{item === 'ALL' ? 'All topics' : item}</button>)}
        </div>
        {error ? <p role="alert" className="mt-4 rounded-xl border border-[#efd9ac] bg-[#fff9ed] p-3 text-xs text-[#76571f]">{error}</p> : null}
        {loading ? <p className="mt-6 text-sm text-[#718078]">Loading the curated corpus…</p> : (
          <div className="mt-4 grid gap-3 md:grid-cols-2">
            {items.map((item) => (
              <article key={`${item.category}-${item.section}-${item.title}`} className="rounded-2xl border border-[#dce5e0] bg-white p-4">
                <div className="flex items-center justify-between gap-3"><span className="font-mono text-[10px] font-bold text-[#2f755b]">{item.section}</span><span className="text-right text-[9px] font-semibold text-[#839088]">{item.act}</span></div>
                <h3 className="mt-3 text-sm font-bold text-[#2b3a34]">{item.title}</h3>
                <p className="mt-2 text-xs leading-5 text-[#66756d]">{item.description}</p>
                {item.document_type?.includes('Model') ? <p className="mt-3 rounded-lg bg-[#fff7e8] p-2 text-[10px] text-[#805f24]">State adoption and the applicable local law must be checked.</p> : null}
                {item.source_url ? (
                  <a href={item.source_url} target="_blank" rel="noreferrer" className="mt-3 inline-flex items-center gap-1.5 text-[10px] font-bold text-[#2f755b] hover:underline">
                    <ExternalLink className="size-3" />{item.source_authority || 'Official source'}{item.effective_from ? ` · effective from ${item.effective_from}` : ''}
                  </a>
                ) : null}
              </article>
            ))}
          </div>
        )}
      </section>

      <div className="mt-8 flex gap-2.5 rounded-2xl bg-[#f1f7f3] p-4 text-xs leading-5 text-[#5d6d64]"><ShieldCheck className="mt-0.5 size-4 shrink-0 text-[#2f755b]" />A source can be genuine and still not apply to your facts. NyayaBot uses sources as grounding, while workflow and professional review decide the appropriate action.</div>
    </div>
  );
}
