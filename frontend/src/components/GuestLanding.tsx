import Image from 'next/image';
import Link from 'next/link';
import {
  ArrowRight,
  BookOpenText,
  BriefcaseBusiness,
  CheckCircle2,
  FileText,
  Grid3X3,
  House,
  MessageCircleMore,
  Search,
  Shield,
  ShoppingCart,
  UsersRound,
} from 'lucide-react';

const categories = [
  { title: 'Cyber Crime', detail: 'Online fraud, hacking, scams', icon: Shield },
  { title: 'Consumer Rights', detail: 'Defective products, refunds, false advertising', icon: ShoppingCart },
  { title: 'Landlord & Tenant', detail: 'Rent issues, deposit, eviction', icon: House },
  { title: 'Employment', detail: 'Salary, harassment, wrongful termination', icon: BriefcaseBusiness },
  { title: 'Family Law', detail: 'Divorce, maintenance, custody', icon: UsersRound },
  { title: 'Legal Notices', detail: 'Create and send notices', icon: FileText },
  { title: 'Other Issues', detail: 'RTI, police complaints, government services', icon: Grid3X3 },
];

const steps = [
  { title: 'Tell Your Story', detail: 'Type, speak or upload evidence.', icon: MessageCircleMore },
  { title: 'Understand Your Options', detail: 'Get a simple explanation with applicable laws.', icon: FileText },
  { title: 'Create Documents', detail: 'Generate ready-to-use notices and complaints.', icon: BookOpenText },
  { title: 'Track Your Progress', detail: 'Save documents and follow your legal journey.', icon: CheckCircle2 },
];

export default function GuestLanding({ loginHref = '/login' }: { loginHref?: string }) {
  return (
    <div className="mx-auto w-full max-w-[1480px] px-4 pb-10 pt-8 sm:px-6 lg:px-8">
      <section className="grid min-w-0 grid-cols-[minmax(0,1fr)] items-center gap-5 lg:min-h-[430px] lg:grid-cols-[minmax(0,1fr)_minmax(0,0.95fr)]" aria-label="NyayaBot introduction">
        <div className="relative z-10 min-w-0 max-w-[640px] py-4 lg:py-8">
          <span className="inline-flex rounded-full bg-gradient-to-r from-[#fff7db] to-[#ffecac] px-4 py-2 text-xs font-bold text-[#765218]">Your AI legal companion</span>
          <h1 className="mt-5 text-[clamp(2.7rem,7vw,6.2rem)] font-extrabold leading-[0.95] tracking-[-0.07em] text-[#111111]">Justice Made<br />Simple.</h1>
          <p className="mt-5 max-w-[520px] text-base leading-7 text-[#68645d] sm:text-lg">Get clear guidance, create legal documents, and track your case — all in one place.</p>
          <Link href={loginHref} className="mt-7 flex max-w-[590px] items-center justify-between gap-4 rounded-2xl border border-[#e5e0d5] bg-white p-2 pl-5 text-sm text-[#77736b] shadow-[0_12px_34px_rgba(88,70,32,0.06)] transition hover:border-[#ecc857]" aria-label="Log in to tell NyayaBot about your legal problem">
            <span className="flex min-w-0 items-center gap-3"><Search className="size-5 shrink-0 text-[#827b6f]" aria-hidden="true" /><span className="truncate">Tell us your legal problem...</span></span>
            <span className="grid size-11 shrink-0 place-items-center rounded-full bg-[#151515] text-white"><ArrowRight className="size-5" aria-hidden="true" /></span>
          </Link>
          <div className="mt-3 flex flex-wrap gap-2 text-[11px] text-[#625f59]">
            <Link href={loginHref} className="rounded-full border border-[#ece6d9] bg-white px-3 py-1.5 hover:border-[#e4bd55]">I was scammed online</Link>
            <Link href={loginHref} className="rounded-full border border-[#ece6d9] bg-white px-3 py-1.5 hover:border-[#e4bd55]">My landlord has my deposit</Link>
            <Link href={loginHref} className="rounded-full border border-[#ece6d9] bg-white px-3 py-1.5 hover:border-[#e4bd55]">My employer hasn’t paid me</Link>
          </div>
        </div>
        <div className="gold-glow relative mx-auto flex h-[320px] min-w-0 w-full max-w-[600px] items-end justify-center overflow-hidden rounded-[38px] sm:h-[440px] lg:rounded-none" aria-hidden="true">
          <Image src="/nyayabot-mascot-hero.png" alt="" width={1240} height={1240} priority className="hero-mascot relative z-10 h-auto w-[87%] max-w-[540px] translate-y-[12%] object-contain" />
        </div>
      </section>

      <section className="mt-5" aria-label="Legal issue categories">
        <div className="soft-scrollbar overflow-x-auto rounded-2xl border border-[#eee7d9] bg-white/85 py-4 shadow-[0_12px_32px_rgba(82,63,21,0.04)]">
          <div className="grid min-w-[980px] grid-cols-7 divide-x divide-[#eee9df] lg:min-w-0">
            {categories.map(({ title, detail, icon: Icon }) => (
              <Link key={title} href={loginHref} className="group flex min-w-0 flex-col px-4 py-1 transition hover:bg-[#fff9e9]" aria-label={`Log in to ask about ${title}`}>
                <Icon className="size-6 text-[#1a1a1a] transition group-hover:text-[#9d6b00]" strokeWidth={1.8} aria-hidden="true" />
                <span className="mt-2 text-xs font-bold text-[#1b1b1b]">{title}</span>
                <span className="mt-1 text-[10px] leading-4 text-[#777169]">{detail}</span>
              </Link>
            ))}
          </div>
        </div>
      </section>

      <section className="mt-5 grid gap-7 rounded-[30px] border border-[#f2e8ce] bg-gradient-to-r from-[#fffdf7] via-[#fff9e8] to-[#fff2c9] p-6 sm:p-8 lg:grid-cols-[minmax(230px,1fr)_minmax(0,2.8fr)] lg:items-center" aria-labelledby="process-title">
        <div>
          <p className="text-[11px] font-bold uppercase tracking-[0.12em] text-[#8a6423]">How it works <span className="ml-2 inline-block h-px w-6 align-middle bg-[#e6ba48]" /></p>
          <h2 id="process-title" className="mt-2 text-3xl font-extrabold leading-[1.05] tracking-[-0.05em] text-[#161616]">From Problem<br className="hidden lg:block" /> to Action</h2>
          <p className="mt-2 max-w-xs text-xs leading-5 text-[#6a655b]">A simple, guided process to help you understand your rights and take the next step.</p>
          <Link href={loginHref} className="mt-4 inline-flex items-center gap-2 rounded-full bg-[#171717] px-5 py-2 text-xs font-bold text-white transition hover:bg-[#303030]">Get Started <ArrowRight className="size-3.5" aria-hidden="true" /></Link>
        </div>
        <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {steps.map(({ title, detail, icon: Icon }, index) => (
            <li key={title} className="relative flex flex-col items-center text-center">
              <div className="relative">
                <span className="absolute -left-3 -top-2 grid size-6 place-items-center rounded-full bg-[#ffdf75] text-[10px] font-bold text-[#171717]">{index + 1}</span>
                <span className="grid size-14 place-items-center rounded-full border border-[#f0e5c8] bg-white text-[#1b1b1b] shadow-sm"><Icon className="size-6" strokeWidth={1.8} aria-hidden="true" /></span>
              </div>
              <h3 className="mt-3 text-xs font-bold text-[#1b1b1b]">{title}</h3>
              <p className="mt-1 max-w-[170px] text-[11px] leading-4 text-[#746e63]">{detail}</p>
              {index < steps.length - 1 ? <ArrowRight className="absolute -right-3 top-5 hidden size-4 text-[#999084] lg:block" aria-hidden="true" /> : null}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
