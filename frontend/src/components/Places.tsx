import { useState } from "react";
import { ArrowLeftRight, ArrowLeft, Car, MapPin, Plus, Trash2 } from "lucide-react";
import { api, type PlacesData } from "../api";
import { useData, useLive } from "../live";
import { fa } from "../util";
import { Badge, Button, Card, Input, Select, Toggle } from "./ui";

const MODES = [
  { value: "", label: "—" },
  { value: "car", label: "ماشین" },
  { value: "taxi", label: "تاکسی/اسنپ" },
  { value: "metro", label: "مترو" },
  { value: "bus", label: "اتوبوس" },
  { value: "walk", label: "پیاده" },
  { value: "bike", label: "دوچرخه/موتور" },
];
const modeLabel = (m: string) => MODES.find((x) => x.value === m)?.label ?? m;

/** Places the user goes to and door-to-door travel times between them (used by the planner and "leave now" reminders). */
export function PlacesCard() {
  const { toast, bump } = useLive();
  const { data } = useData<PlacesData>("/api/places", ["places"]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("other");
  const [aliases, setAliases] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [minutes, setMinutes] = useState("");
  const [mode, setMode] = useState("");
  const [both, setBoth] = useState(true);

  if (!data) return null;
  const kinds = Object.entries(data.kinds).map(([value, label]) => ({ value, label }));
  const placeOptions = [{ value: "", label: "انتخاب…" }, ...data.places.map((p) => ({ value: String(p.id), label: p.name }))];

  const run = async (fn: () => Promise<unknown>, ok: string) => {
    try {
      await fn();
      bump("places", "schedule");
      toast(ok, "success");
    } catch (e) {
      toast((e as Error).message, "error");
    }
  };
  const addPlace = () =>
    name.trim() &&
    run(async () => {
      await api.post("/api/places", {
        name: name.trim(),
        kind,
        aliases: aliases.split(/[,،]/).map((a) => a.trim()).filter(Boolean),
      });
      setName("");
      setAliases("");
      setKind("other");
    }, "مکان اضافه شد");
  const addTravel = () =>
    from && to && minutes &&
    run(async () => {
      await api.put("/api/travel", { from_id: Number(from), to_id: Number(to), minutes: Number(minutes), mode, both_ways: both });
      setMinutes("");
    }, "زمان مسیر ذخیره شد");

  return (
    <Card title="مکان‌ها و رفت‌وآمد" icon={<MapPin className="size-4 text-rose-500" />}>
      <p className="mb-3 text-xs leading-6 text-slate-500 dark:text-slate-400">
        اگر محل کلاس یا قرار با یکی از این مکان‌ها (یا اسم مستعارش) جور باشد، زمان حاضر شدن و مسیر خودکار در برنامه حساب می‌شود
        و موقع راه افتادن یادآوری می‌کنم. لازم نیست اینجا پر کنی؛ در چت هم بگویی («از خونه تا شرکت ۴۰ دقیقه با مترو») ثبت می‌شود.
      </p>

      <div className="space-y-2">
        {data.places.map((p) => (
          <div key={p.id} className="flex flex-wrap items-center gap-2 rounded-xl border border-slate-100 px-3 py-2 dark:border-slate-800">
            <span className="font-medium">{p.name}</span>
            <Badge>{data.kinds[p.kind] ?? p.kind}</Badge>
            {p.aliases.map((a) => (
              <span key={a} className="rounded-full bg-slate-100 px-2 py-0.5 text-[11px] text-slate-500 dark:bg-slate-800">{a}</span>
            ))}
            {p.from_home_minutes !== null && (
              <span className="text-xs text-slate-500">از خانه {fa(p.from_home_minutes)} دقیقه</span>
            )}
            <button
              className="ms-auto text-slate-400 hover:text-rose-600"
              title="حذف"
              onClick={() => window.confirm(`«${p.name}» حذف شود؟`) && run(() => api.del(`/api/places/${p.id}`), "حذف شد")}
            >
              <Trash2 className="size-4" />
            </button>
          </div>
        ))}
        {data.places.length === 0 && <p className="text-sm text-slate-400">هنوز مکانی ثبت نشده — از «خانه» شروع کن.</p>}
      </div>
      <div className="mt-3 grid gap-2 sm:grid-cols-[1fr_9rem_1fr_auto]">
        <Input placeholder="اسم مکان (مثلاً شرکت)" value={name} onChange={(e) => setName(e.target.value)} />
        <Select value={kind} onChange={setKind} options={kinds} />
        <Input placeholder="اسم‌های دیگر، با ویرگول (دفتر، کار)" value={aliases} onChange={(e) => setAliases(e.target.value)} />
        <Button variant="soft" onClick={addPlace} disabled={!name.trim()}><Plus className="size-4" /> افزودن</Button>
      </div>

      {data.places.length >= 2 && (
        <>
          <h3 className="mb-2 mt-5 flex items-center gap-2 text-sm font-semibold"><Car className="size-4 text-slate-400" /> زمان مسیرها (در به در)</h3>
          <div className="space-y-1.5">
            {data.travel.map((t) => (
              <div key={t.id} className="flex flex-wrap items-center gap-2 text-sm">
                <span>{t.from}</span>
                {t.both_ways ? <ArrowLeftRight className="size-3.5 text-slate-400" /> : <ArrowLeft className="size-3.5 text-slate-400" />}
                <span>{t.to}</span>
                <span className="font-semibold">{fa(t.minutes)} دقیقه</span>
                {t.mode && <span className="text-xs text-slate-500">({modeLabel(t.mode)})</span>}
                {t.samples > 1 && <span className="text-[11px] text-slate-400">میانگین {fa(t.samples)} بار</span>}
                <button className="ms-auto text-slate-400 hover:text-rose-600" title="حذف" onClick={() => run(() => api.del(`/api/travel/${t.id}`), "حذف شد")}>
                  <Trash2 className="size-4" />
                </button>
              </div>
            ))}
          </div>
          <div className="mt-3 grid grid-cols-[1fr_auto_1fr] items-center gap-2 sm:grid-cols-[1fr_auto_1fr_6rem_8rem]">
            <Select value={from} onChange={setFrom} options={placeOptions} />
            <span className="text-sm text-slate-400">تا</span>
            <Select value={to} onChange={setTo} options={placeOptions} />
            <Input type="number" min={0} placeholder="دقیقه" value={minutes} onChange={(e) => setMinutes(e.target.value)} />
            <Select value={mode} onChange={setMode} options={MODES} />
          </div>
          <div className="mt-2 flex items-center justify-between gap-2">
            <label className="flex items-center gap-2 text-xs text-slate-500">
              <Toggle checked={both} onChange={setBoth} label="رفت و برگشت" /> رفت و برگشت یکی است
            </label>
            <Button variant="soft" onClick={addTravel} disabled={!from || !to || !minutes || from === to}>ذخیره زمان مسیر</Button>
          </div>
        </>
      )}
    </Card>
  );
}
