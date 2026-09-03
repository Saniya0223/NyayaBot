'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { Download, FileText, FolderOpen, Plus } from 'lucide-react';
import { absoluteDocumentUrl, DocumentListItem, fetchDocuments } from '@/lib/api';

export default function DocumentsPage() {
  const [documents, setDocuments] = useState<DocumentListItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchDocuments().then((items) => { if (active) setDocuments(items); }).catch((caught) => { if (active) setError(caught instanceof Error ? caught.message : 'Documents could not be loaded.'); }).finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  const grouped = useMemo(() => documents.reduce<Record<string, DocumentListItem[]>>((result, item) => { (result[item.case_title] ||= []).push(item); return result; }, {}), [documents]);

  return (
    <div className="mx-auto max-w-6xl px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
      <header className="flex flex-col justify-between gap-5 sm:flex-row sm:items-end"><div><p className="flex items-center gap-2 text-xs font-bold uppercase tracking-[0.13em] text-[#2f755b]"><FileText className="size-4" />Case documents</p><h1 className="mt-2 text-3xl font-bold tracking-[-0.04em] text-[#17231f]">Documents</h1><p className="mt-2 text-sm text-[#6b7872]">Drafts are grouped by the case that produced them.</p></div><Link href="/" className="flex w-fit items-center gap-2 rounded-xl bg-[#174e3b] px-4 py-2.5 text-xs font-bold text-white"><Plus className="size-4" />Ask NyayaBot</Link></header>
      {error ? <p role="alert" className="mt-6 rounded-2xl bg-[#fff1ef] p-4 text-sm text-[#97473b]">{error}</p> : null}
      {loading ? <p className="mt-10 text-sm text-[#718078]">Loading document records…</p> : null}
      {!loading && documents.length === 0 ? <div className="mt-8 rounded-3xl border border-dashed border-[#cbd8d0] bg-white p-12 text-center"><FolderOpen className="mx-auto size-10 text-[#91a197]" /><h2 className="mt-4 text-lg font-bold text-[#2a3933]">No documents yet</h2><p className="mt-2 text-sm text-[#718078]">NyayaBot recommends the right document from the conversation and current stage.</p></div> : null}
      <div className="mt-8 space-y-7">{Object.entries(grouped).map(([caseTitle, items]) => <section key={caseTitle}><h2 className="text-sm font-bold text-[#2b3a34]">{caseTitle}</h2><div className="mt-3 grid gap-3 md:grid-cols-2">{items.map((item) => <article key={item.id} className="rounded-2xl border border-[#dde5e0] bg-white p-4"><div className="flex items-start justify-between gap-3"><div><span className="rounded-full bg-[#edf3ef] px-2 py-1 text-[9px] font-bold uppercase tracking-wide text-[#3e6e57]">{item.status}</span><h3 className="mt-3 text-sm font-bold text-[#27372f]">{item.title}</h3><p className="mt-1 text-[10px] text-[#829088]">Generated {new Date(item.created_at).toLocaleDateString('en-IN', { dateStyle: 'medium' })}</p></div><FileText className="size-5 text-[#7c9487]" /></div><div className="mt-4 flex gap-2">{item.pdf_download_url ? <a href={absoluteDocumentUrl(item.pdf_download_url)} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded-lg bg-[#174e3b] px-3 py-2 text-[11px] font-bold text-white"><Download className="size-3.5" />PDF</a> : null}{item.docx_download_url ? <a href={absoluteDocumentUrl(item.docx_download_url)} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 rounded-lg border border-[#ccd8d1] px-3 py-2 text-[11px] font-bold text-[#174e3b]"><Download className="size-3.5" />DOCX</a> : null}<Link href={`/?case=${encodeURIComponent(item.case_id)}`} className="ml-auto rounded-lg px-3 py-2 text-[11px] font-bold text-[#5c6b64] hover:bg-[#f1f5f2]">Open case</Link></div></article>)}</div></section>)}</div>
    </div>
  );
}
