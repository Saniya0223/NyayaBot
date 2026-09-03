'use client';

import React, { useState } from 'react';
import { generateDocument, DocumentResponse } from '@/lib/api';
import { FileText, Download, Copy, Check, Sparkles, Scale } from 'lucide-react';

interface DocumentViewerProps {
  caseId: string;
  defaultCategory: string;
}

export default function DocumentViewer({ caseId, defaultCategory }: DocumentViewerProps) {
  const [selectedDocType, setSelectedDocType] = useState<string>(
    defaultCategory === 'TENANCY' 
      ? 'TENANT_DEMAND_NOTICE' 
      : defaultCategory === 'RTI' 
      ? 'RTI_SEC6' 
      : 'FORMAL_LEGAL_NOTICE'
  );
  const [documentData, setDocumentData] = useState<DocumentResponse | null>(null);
  const [isGenerating, setIsGenerating] = useState(false);
  const [copied, setCopied] = useState(false);

  const docOptions = [
    { type: 'FORMAL_LEGAL_NOTICE', label: '15-Day Legal Demand Notice', desc: 'Mandatory pre-litigation demand under Consumer Protection Act 2019.' },
    { type: 'EDAAKHIL_COMPLAINT', label: 'Formal e-Daakhil Complaint (Sec 35 CPA)', desc: 'Complete pleading memo, prayer clause & verification affidavit for court filing.' },
    { type: 'TENANT_DEMAND_NOTICE', label: 'Tenancy Security Deposit Demand', desc: 'Formal notice citing Model Tenancy & State Rent Rules for deposit recovery.' },
    { type: 'RTI_SEC6', label: 'Section 6(1) RTI Application', desc: 'Format for public authority PIO with specific information questions.' }
  ];

  const handleGenerate = async (typeToGen?: string) => {
    const docType = typeToGen || selectedDocType;
    setIsGenerating(true);
    try {
      const result = await generateDocument(caseId, docType);
      setDocumentData(result);
    } catch (err) {
      console.error('Failed to generate document', err);
    } finally {
      setIsGenerating(false);
    }
  };

  const handleCopy = () => {
    if (!documentData) return;
    // Strip HTML tags for clean clipboard text
    const text = documentData.content_html.replace(/<[^>]*>?/gm, '\n').replace(/\n\s*\n/g, '\n\n');
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleDownloadPdf = () => {
    if (documentData?.pdf_download_url) {
      const fullUrl = `http://localhost:8000${documentData.pdf_download_url}`;
      window.open(fullUrl, '_blank');
    }
  };

  return (
    <div className="space-y-6">
      
      {/* Template Selector Bar */}
      <div className="p-5 rounded-2xl bg-slate-900/90 border border-slate-800 space-y-4">
        <div>
          <h3 className="font-semibold text-white text-base flex items-center gap-2">
            <FileText className="w-5 h-5 text-amber-400" />
            <span>Deterministic Legal Document Generation Engine</span>
          </h3>
          <p className="text-xs text-slate-400 mt-1">
            Generates standardized, court-compliant legal notices and complaints with 0% hallucination.
          </p>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
          {docOptions.map((opt) => {
            const isSelected = selectedDocType === opt.type;
            return (
              <button
                key={opt.type}
                onClick={() => {
                  setSelectedDocType(opt.type);
                  handleGenerate(opt.type);
                }}
                className={`text-left p-3.5 rounded-xl border transition-all ${
                  isSelected
                    ? 'bg-amber-500/10 border-amber-500/40 shadow-lg shadow-amber-500/5 ring-1 ring-amber-500/30'
                    : 'bg-slate-950 border-slate-800 hover:border-slate-700'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className={`text-xs font-bold ${isSelected ? 'text-amber-400' : 'text-white'}`}>
                    {opt.label}
                  </span>
                </div>
                <p className="text-[11px] text-slate-400 leading-snug">{opt.desc}</p>
              </button>
            );
          })}
        </div>

        <div className="pt-2 flex justify-end">
          <button
            onClick={() => handleGenerate()}
            disabled={isGenerating}
            className="flex items-center gap-2 px-5 py-2 rounded-xl bg-amber-500 hover:bg-amber-400 disabled:opacity-50 text-slate-950 font-semibold text-xs shadow-lg shadow-amber-500/10 transition-colors"
          >
            <Sparkles className="w-4 h-4" />
            <span>{isGenerating ? 'Synthesizing Document...' : 'Compile & Render Document'}</span>
          </button>
        </div>
      </div>

      {/* Rendered Document Preview & Actions */}
      {documentData ? (
        <div className="space-y-4">
          
          {/* Action Toolbar */}
          <div className="flex flex-wrap items-center justify-between gap-3 p-3.5 rounded-xl bg-slate-900 border border-slate-800">
            <div className="flex items-center gap-2">
              <span className="text-xs font-semibold text-white">{documentData.title}</span>
              <span className="text-[10px] px-2 py-0.5 rounded bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                PDF Ready
              </span>
            </div>

            <div className="flex items-center gap-2">
              <button
                onClick={handleCopy}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs transition-colors"
              >
                {copied ? <Check className="w-3.5 h-3.5 text-emerald-400" /> : <Copy className="w-3.5 h-3.5" />}
                <span>{copied ? 'Copied Text' : 'Copy Text'}</span>
              </button>

              <button
                onClick={handleDownloadPdf}
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-lg bg-amber-500 hover:bg-amber-400 text-slate-950 font-semibold text-xs shadow transition-colors"
              >
                <Download className="w-3.5 h-3.5" />
                <span>Download Official Court PDF</span>
              </button>
            </div>
          </div>

          {/* Statutory Grounding Citations */}
          {documentData.statutory_citations.length > 0 && (
            <div className="p-3.5 rounded-xl bg-slate-950 border border-slate-800 flex flex-wrap items-center gap-2 text-xs">
              <span className="text-slate-400 font-semibold flex items-center gap-1.5">
                <Scale className="w-3.5 h-3.5 text-amber-400" /> Verified Legal Grounding:
              </span>
              {documentData.statutory_citations.map((c, i) => (
                <span key={i} className="px-2.5 py-1 rounded-md bg-slate-900 border border-slate-700/80 text-amber-300 font-mono text-[11px]">
                  {c.section} ({c.title})
                </span>
              ))}
            </div>
          )}

          {/* Document Content Viewport (Stylized Paper Sheet) */}
          <div className="p-8 sm:p-12 rounded-2xl bg-white text-black shadow-2xl overflow-x-auto min-h-[600px] border border-slate-300">
            <div 
              className="prose max-w-none text-black font-serif text-sm leading-relaxed"
              dangerouslySetInnerHTML={{ __html: documentData.content_html }}
            />
          </div>

        </div>
      ) : (
        <div className="text-center py-16 p-6 rounded-2xl bg-slate-900/40 border border-dashed border-slate-800 space-y-3">
          <FileText className="w-12 h-12 text-slate-600 mx-auto" />
          <h4 className="text-sm font-semibold text-slate-300">No Document Rendered Yet</h4>
          <p className="text-xs text-slate-500 max-w-md mx-auto">
            Select a document type above and click &quot;Compile &amp; Render Document&quot; to assemble your formal notice or complaint.
          </p>
          <button
            onClick={() => handleGenerate()}
            disabled={isGenerating}
            className="inline-flex items-center gap-2 px-4 py-2 rounded-xl bg-slate-800 hover:bg-slate-700 text-amber-400 text-xs font-semibold transition-colors mt-2"
          >
            <Sparkles className="w-3.5 h-3.5" />
            <span>Generate Document Now</span>
          </button>
        </div>
      )}

    </div>
  );
}
