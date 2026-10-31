"use client";

import * as Dialog from "@radix-ui/react-dialog";
import * as Tabs from "@radix-ui/react-tabs";
import * as Tooltip from "@radix-ui/react-tooltip";
import Image from "next/image";
import DocumentStatus from "./components/DocumentStatus";
import AcademicAssistant, {academicRequest} from "./components/AcademicAssistant";
import ReaderPanel from "./components/ReaderPanel";
import PaperPage from "./paper/PaperPage";
import "./academic.css";
import {workspaceMode, sourceEditIssues, isDemoCandidateId, baiduScholarUrl, type WorkspaceDocument, type WorkspaceJob} from "./lib/workspace-state";
import {publishParagraphCitation, type CitationProjection} from "./lib/citation-sync";
import "./repair.css";
import "./ui-refinement.css";
import {
  Archive, BarChart3, BookOpenText, Check, CheckCircle2, ChevronDown, ChevronRight, ChevronUp, CircleAlert,
  Clock3, Cloud, Download, ExternalLink, FileChartColumn, FileSearch, Files, FolderOpen, Grid2X2,
  History, Image as ImageIcon, LayoutDashboard, Library, MessageCircle, MoreHorizontal, PanelLeftClose, PanelLeftOpen, PanelRight,
  Pause, Pencil, Play, Plus, Power, RotateCcw, Search, Server, Settings, ShieldCheck, Sparkles, Trash2, Upload, X,
} from "lucide-react";
import { ChangeEvent, createContext, DragEvent, Fragment, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";

type Screen = "home" | "workspace" | "library" | "trash" | "settings" | "paper";
type CandidateStatus = "pending" | "accepted" | "rejected";
type CandidateReuseStatus = "safe_open" | "attribution_required" | "citation_only" | "unknown";
type Candidate = { doi?:string|null; year?:number|null; journal?:string|null; citationSelected?:boolean; id: string; title: string; source: string; license: string; licenseUrl?:string|null; licenseVerified?:boolean; verificationSource?:"provider_metadata"|"manual"|null; reuseStatus?:CandidateReuseStatus; score: number; reuse: "safe" | "cite" | "warn"; variant: string; status: CandidateStatus; previewUrl?:string|null; sourceUrl?:string; author?:string|null; accessedAt?:string|null; checksum?:string|null; rationale?:string|null };
type CandidateApiRecord = { doi?:string|null; year?:number|null; journal?:string|null; citation_action?:string|null; id:string; title:string; provider:string; license?:{name?:string;url?:string|null;verified?:boolean;verification_source?:"provider_metadata"|"manual"|null}; score:number; reuse_status:string; preview_url?:string|null; source_url?:string; author?:string|null; accessed_at?:string|null; checksum?:string|null; rationale?:string|null; decision_action?:string|null };
type ProjectSummary = { manuscript_document_id?:string|null; manuscript_required?:boolean; documents?:WorkspaceDocument[]; jobs?:WorkspaceJob[]; id:string; name:string; status:string; updated:string; progress:number; quality_mode?:string; trashed_at?:string|null };
type AnchorRecord = { id:string; section:string; page:number|null; paragraph_index:number; text:string; ocr_confidence:number|null; ocr_rotation:number|null; ocr_confirmed:boolean; sensitivity:string[] };
type DatasetSheetProfile = { name:string; rows:number; columns:Array<{name:string;dtype:string}>; recommended_charts:string[] };
type DatasetProfile = { id:string; filename:string; rows:number; columns:Array<{name:string;dtype:string}>; recommended_charts:string[]; selected_sheet?:string|null; sheets?:DatasetSheetProfile[] };
type ChartRecord = { id:string; dataset_id:string; sheet_name?:string|null; anchor_id?:string|null; chart_type:string; x:string; y:string; group?:string|null; title:string; independent_unit?:string|null; error_bar?:string|null; statistical_test?:string|null; width_mm?:number; height_mm?:number; font_family?:string; font_size_pt?:number; title_size_pt?:number; x_label?:string|null; y_label?:string|null; grid_mode?:string; x_scale?:string; y_scale?:string; x_min?:number|null; x_max?:number|null; y_min?:number|null; y_max?:number|null; tick_count?:number; show_minor_ticks?:boolean; legend_position?:string; palette?:string; line_width?:number; marker?:string; show_data_labels?:boolean; preview_url:string; download_urls?:Record<string,string>; warnings:string[] };
type ChartBlocker = { id:string; title:string; warnings:string[] };
type ChartStyleState = { widthMm:number; heightMm:number; fontFamily:string; fontSizePt:number; titleSizePt:number; xLabel:string; yLabel:string; gridMode:string; xScale:string; yScale:string; xMin:string; xMax:string; yMin:string; yMax:string; tickCount:number; showMinorTicks:boolean; legendPosition:string; palette:string; lineWidth:number; marker:string; showDataLabels:boolean };
type CompositionRecord = { id:string; anchor_id?:string|null; caption:string; template:"1x2"|"2x2"|"hero_side"|"vertical"; panels:Array<{kind:"asset"|"chart";source_id:string;label:string;crop_zoom:number}>; gap_px:number; aspect_ratio:"auto"|"1:1"|"4:3"|"16:9"; width_mm:89|180; dpi:300|600; preview_url:string; download_urls:Record<string,string>; warnings:string[] };
type PrivacyPreview = { findings:string[]; original:string; redacted:string; requires_confirmation:boolean };
type DocumentRevision = { id:string; reason:string; action:string; changed_count:number; created_at:string };
type ModelProfile = { id:string; name:string; kind:"local"|"online"; base_url:string; model:string; enabled:boolean; has_api_key:boolean; ready:boolean };
type CompositionPanel = { key:string; kind:"asset"|"chart"; id:string; title:string; previewUrl:string|null };
type JobRecord = { id:string; project_id:string; project_name?:string; kind:string; state:"queued"|"running"|"paused"|"completed"|"failed"|"cancelled"; stage:string; progress:number; message?:string|null; result?:Record<string,unknown>|null; created_at:string; updated_at:string };
type TranslatedOption = { value:string; label:string; meaning:string };
type UpdateStatus = { current_version?:string; configured?:boolean; manifest_url?:string; message?:string };
type UpdateCheckResult = { current_version:string; latest_version:string; update_available:boolean; release_url?:string|null; checked_at:string; message:string };
type LocalAccount = { id:string; username:string; display_name:string; preferences:{annotations_enabled?:boolean;last_screen?:Screen}; must_change_password:boolean };
type RuntimeHealth = { status?:string; version?:string; database?:string; projects_root?:string; ocr_languages?:string[]; ocr?:{available:boolean;executable?:string|null;languages:string[];required_ready:boolean;message:string} };

const AnnotationContext = createContext(true);

const jobKindLabels: Record<string,string> = {
  document_analysis: "文档解析与 OCR",
  chart_render: "科研图渲染",
  composition_render: "多面板组图",
  project_export: "项目导出",
};

const chartTypeMeanings: Record<string,string> = {
  grouped_bar: "分组柱状图：比较多个类别或实验组之间的数值差异",
  box: "箱线图：展示中位数、四分位数、离群值和数据分布",
  scatter: "散点图：观察两个连续变量之间的关系",
  heatmap: "相关性热图：使用数据集中的全部数值字段，以颜色强度展示两两相关性",
  line: "折线图：展示连续顺序或时间维度上的变化趋势",
};

const errorBarMeanings: Record<string,string> = {
  "": "尚未指定误差线或不确定性表达方式",
  ci95: "95% 置信区间：估计参数在重复抽样下的区间不确定性",
  sd: "标准差：描述观测值围绕均值的离散程度",
  se: "标准误：描述样本均值估计的不确定性",
  none: "不适用：直接展示原始观测点或完整分布",
};

const formatMeanings: Record<string,string> = {
  svg: "可缩放矢量图：适合继续编辑和排版",
  pdf: "便携文档格式：适合投稿、打印和跨平台查看",
  png: "无损位图格式：适合网页预览和普通文档",
  tiff: "高质量标记图像格式：常用于期刊投稿和印刷",
  xlsx: "可编辑 Excel 工作簿：包含原始数据、公式追踪与原生 Office 图表",
  package: "跨项目图表包：包含数据、ChartSpec、复现脚本与全部输出",
};

const defaultChartStyle: ChartStyleState = {
  widthMm:180, heightMm:110, fontFamily:"Arial", fontSizePt:8, titleSizePt:11,
  xLabel:"", yLabel:"", gridMode:"none", xScale:"linear", yScale:"linear",
  xMin:"", xMax:"", yMin:"", yMax:"", tickCount:6, showMinorTicks:false,
  legendPosition:"auto", palette:"nature", lineWidth:1.5, marker:"auto", showDataLabels:false,
};

const commonColumnMeanings: Record<string,string> = {
  temperature: "温度：通常作为实验条件、连续变量或横轴",
  conversion: "转化率：反应物转化为目标产物或其他产物的比例",
  material: "材料或试样类型：用于区分不同处理组",
  batch: "批次：用于标识独立制备或独立实验单位",
  time: "时间：用于展示随时间变化的趋势",
  group: "分组：用于区分实验组、对照组或类别",
  value: "数值：待分析或绘制的响应变量",
};

function datasetColumnOptions(profile:{columns:Array<{name:string;dtype:string}>} | null, emptyLabel:string, emptyMeaning:string):TranslatedOption[] {
  const columns = profile?.columns || [];
  return [
    {value:"", label:emptyLabel, meaning:emptyMeaning},
    ...columns.map(column=>({
      value:column.name,
      label:column.name,
      meaning:commonColumnMeanings[column.name.toLowerCase()] || `数据字段“${column.name}”，类型为 ${column.dtype}`,
    })),
  ];
}

const demoCandidates: Candidate[] = [
  { id: "c1", previewUrl:"/demo/figure-1.svg", title: "城市热岛的空间分布", source: "内置演示", license: "示例图，非检索结果", score: 94, reuse: "safe", variant: "", status: "pending" },
  { id: "c2", previewUrl:"/demo/figure-2.svg", title: "绿色基础设施剖面示例", source: "内置演示", license: "示例图，非检索结果", score: 88, reuse: "cite", variant: "variant-b", status: "pending" },
  { id: "c3", previewUrl:"/demo/figure-3.svg", title: "热暴露与植被覆盖研究图", source: "内置演示", license: "示例图，非检索结果", score: 82, reuse: "cite", variant: "variant-c", status: "pending" },
];

const demoParagraphs = [
  "城市扩张改变了地表能量平衡，使高密度建成区在夜间持续释放白天储存的热量。已有研究表明，热暴露并非均匀分布，而是与地表覆盖、建筑形态及社会脆弱性共同相关。",
  "本研究整合多时相遥感影像、街区尺度形态指标与地面温度观测，构建了可解释的热风险评估框架。该框架不仅识别高温热点，还进一步区分由不透水面、低植被覆盖和通风受阻所驱动的风险类型。",
  "在三个代表性城市中，树冠覆盖率每提高 10%，夏季日间地表温度中位数下降 1.3–1.8 °C。降温效应在连续绿廊附近最明显，并随距绿地边缘的距离增加而快速衰减。",
  "结果说明，单纯增加绿量并不足以获得稳定的降温收益。绿地的空间连续性、冠层高度和与主导风向的关系，应共同进入城市更新与热适应策略。",
];

function TooltipButton({ label, children, className = "icon-btn", onClick, pressed, disabled = false }: { label: string; children: React.ReactNode; className?: string; onClick?: () => void; pressed?: boolean; disabled?:boolean }) {
  return <Tooltip.Root><Tooltip.Trigger asChild><button className={className} aria-label={label} aria-pressed={pressed} disabled={disabled} onClick={onClick}>{children}</button></Tooltip.Trigger><Tooltip.Portal><Tooltip.Content sideOffset={7} style={{background:'#25332b',color:'white',fontSize:11,padding:'6px 8px',borderRadius:7,zIndex:200}}>{label}</Tooltip.Content></Tooltip.Portal></Tooltip.Root>;
}

function TranslationTooltip({ term, meaning, children, side = "top" }: { term:string; meaning:string; children:React.ReactElement; side?:"top"|"right"|"bottom"|"left" }) {
  const annotationsEnabled = useContext(AnnotationContext);
  if (!annotationsEnabled) return children;
  return <Tooltip.Root delayDuration={560}>
    <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
    <Tooltip.Portal>
      <Tooltip.Content className="translation-tooltip" side={side} sideOffset={9} collisionPadding={16} avoidCollisions sticky="partial">
        <span className="translation-term">{term}</span>
        <span>{meaning}</span>
        <Tooltip.Arrow className="translation-tooltip-arrow"/>
      </Tooltip.Content>
    </Tooltip.Portal>
  </Tooltip.Root>;
}

function TranslatedTerm({ term, meaning, className = "" }: { term:string; meaning:string; className?:string }) {
  return <TranslationTooltip term={term} meaning={meaning}><span className={`translated-term ${className}`} tabIndex={0}>{term}</span></TranslationTooltip>;
}

function TranslatedSelect({ id, value, options, onChange, ariaLabel }: { id:string; value:string; options:TranslatedOption[]; onChange:(value:string)=>void; ariaLabel:string }) {
  const annotationsEnabled = useContext(AnnotationContext);
  const [open, setOpen] = useState(false);
  const [highlighted, setHighlighted] = useState(value || options[0]?.value || "");
  const rootRef = useRef<HTMLDivElement>(null);
  const activeValue = highlighted || value || options[0]?.value || "";
  const selectedOption = options.find(option=>option.value===value) || options[0];
  const activeOption = options.find(option=>option.value===activeValue) || selectedOption;

  useEffect(() => {
    if (!open) return;
    const closeOutside = (event:PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", closeOutside);
    return () => document.removeEventListener("pointerdown", closeOutside);
  }, [open]);

  const moveHighlight = (direction:1|-1) => {
    const currentIndex = Math.max(0, options.findIndex(option=>option.value===activeValue));
    setHighlighted(options[(currentIndex + direction + options.length) % options.length]?.value || "");
  };
  const choose = (next:string) => {
    onChange(next);
    setHighlighted(next);
    setOpen(false);
  };

  return <div className={`translated-select ${annotationsEnabled?'':'annotations-off'}`} ref={rootRef}>
    <button
      id={id}
      type="button"
      className="input translated-select-trigger"
      role="combobox"
      aria-label={ariaLabel}
      aria-expanded={open}
      aria-controls={`${id}-listbox`}
      aria-haspopup="listbox"
      onClick={()=>{setHighlighted(value || options[0]?.value || "");setOpen(state=>!state)}}
      onKeyDown={event=>{
        if (event.key === "ArrowDown" || event.key === "ArrowUp") {
          event.preventDefault();
          if (!open) setOpen(true);
          moveHighlight(event.key === "ArrowDown" ? 1 : -1);
        } else if (event.key === "Enter" && open) {
          event.preventDefault();
          choose(activeValue);
        } else if (event.key === "Escape") {
          setOpen(false);
        }
      }}
    >
      <span>{selectedOption?.label || value}</span><ChevronDown size={15} aria-hidden="true"/>
    </button>
    {open && <div className="translated-select-popover">
      <div id={`${id}-listbox`} className="translated-option-list" role="listbox" aria-label={ariaLabel}>
        {options.map(option=><button
          type="button"
          role="option"
          aria-selected={option.value===value}
          className={`translated-option ${option.value===value?'selected':''} ${option.value===activeValue?'highlighted':''}`}
          key={option.value}
          onPointerEnter={()=>setHighlighted(option.value)}
          onFocus={()=>setHighlighted(option.value)}
          onClick={()=>choose(option.value)}
        >{option.label}</button>)}
      </div>
      {annotationsEnabled && <aside className="translated-option-preview" aria-live="polite" key={activeValue}>
        <strong>{activeOption?.label || activeValue}</strong>
        <span>{activeOption?.meaning || "该选项暂无中文说明"}</span>
      </aside>}
    </div>}
  </div>;
}

function defaultDatasetAxes(profile: {columns:Array<{name:string;dtype:string}>}) {
  const x = profile.columns[0]?.name || "";
  const numeric = profile.columns.filter(column => /int|float|number/i.test(column.dtype));
  const y = numeric.find(column => column.name !== x)?.name
    || numeric[0]?.name
    || profile.columns.find(column => column.name !== x)?.name
    || "";
  return {x, y};
}

function candidateFromApi(item: CandidateApiRecord): Candidate {
  const reuse = item.reuse_status === "citation_only" ? "cite" : item.reuse_status === "unknown" ? "warn" : "safe";
  return {
    doi:item.doi,year:item.year,journal:item.journal,id:item.id, title:item.title, source:item.provider, license:item.license?.name || "许可未知",
    score:Math.round(item.score <= 1 ? item.score * 100 : item.score),
    reuse,
    reuseStatus:item.reuse_status as CandidateReuseStatus,
    variant:"", status:item.decision_action === "accept" && reuse === "safe" ? "accepted" : item.decision_action === "reject" ? "rejected" : "pending",
    previewUrl:item.preview_url ? `/api${item.preview_url}` : null, sourceUrl:item.source_url, author:item.author,
    licenseUrl:item.license?.url, licenseVerified:item.license?.verified, verificationSource:item.license?.verification_source || (item.license?.verified ? "provider_metadata" : null),
    accessedAt:item.accessed_at, checksum:item.checksum, rationale:item.rationale, citationSelected:item.citation_action==="accept",
  };
}

function formatAccessedAt(value?: string | null) {
  if (!value) return "未记录";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString("zh-CN", {hour12:false});
}

function normalizedErrorBar(value?: string | null) {
  if (value === "95% CI") return "ci95";
  if (value === "SD") return "sd";
  if (value === "SE") return "se";
  return value || "";
}

export function WorkbenchApp() {
  const [screen, setScreenValue] = useState<Screen>("home");
  const [paperMounted, setPaperMounted] = useState(false);
  const [paperDirty, setPaperDirty] = useState(false);
  const setScreen = useCallback((next: Screen) => {
    if(next === "paper")setPaperMounted(true);
    setScreenValue(next);
  }, []);
  const [dragging, setDragging] = useState(false);
  const [selectedParagraph, setSelectedParagraph] = useState(1);
  const [candidates, setCandidates] = useState(demoCandidates);
  const [uploadBusy,setUploadBusy] = useState(false);
  const [referenceOpen,setReferenceOpen] = useState(false);
  const [referenceDraft,setReferenceDraft] = useState({title:"",sourceUrl:"",author:""});
  const [referenceSaving,setReferenceSaving] = useState(false);
  const [citationSaving,setCitationSaving] = useState<string|null>(null);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [privacyOpen, setPrivacyOpen] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [activeTab, setActiveTab] = useState("assets");
  const [query, setQuery] = useState("");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [apiReady, setApiReady] = useState(false);
  const [runtimeHealth, setRuntimeHealth] = useState<RuntimeHealth>({});
  const [currentProject, setCurrentProject] = useState<ProjectSummary | null>(null);
  const [anchors, setAnchors] = useState<AnchorRecord[]>([]);
  const [citationProjection,setCitationProjection] = useState<CitationProjection|null>(null);
  const [citationError,setCitationError] = useState("");
  const projectionRequestRef=useRef(0);
  const [materialDatasets,setMaterialDatasets]=useState<DatasetProfile[]>([]);
  const activeProjectRef = useRef<string|null>(null);
  activeProjectRef.current = currentProject?.id || null;
  const refreshCitationProjection = useCallback(async (projectId:string) => {
    const request=++projectionRequestRef.current;
    try{
      const result = await academicRequest(`/api/projects/${projectId}/citation-projection`);
      if(activeProjectRef.current===projectId && request===projectionRequestRef.current){setCitationProjection(result);setCitationError("");}
      return result as CitationProjection;
    }catch(error){
      if(activeProjectRef.current===projectId && request===projectionRequestRef.current)setCitationError(error instanceof Error?error.message:"引用编号读取失败");
      throw error;
    }
  },[]);
  useEffect(()=>{
    setCitationProjection(null);setCitationError("");
    if(!currentProject?.id)return;
    const requestGate=projectionRequestRef;
    refreshCitationProjection(currentProject.id).catch(()=>{});
    return()=>{requestGate.current++;};
  },[currentProject?.id,anchors,refreshCitationProjection]);
  useEffect(()=>{let live=true;setMaterialDatasets([]);if(currentProject?.id)academicRequest(`/api/projects/${currentProject.id}/datasets`).then(rows=>{if(live)setMaterialDatasets(rows);}).catch(()=>{});return()=>{live=false;};},[currentProject?.id]);
  const openMaterialUpload = () => fileInput.current?.click();
  const [dataset, setDataset] = useState<DatasetProfile | null>(null);
  const [sheetName, setSheetName] = useState("");
  const [chartType, setChartType] = useState("grouped_bar");
  const [xField, setXField] = useState("");
  const [yField, setYField] = useState("");
  const [groupField, setGroupField] = useState("");
  const [chartTitle, setChartTitle] = useState("");
  const [independentUnit, setIndependentUnit] = useState("");
  const [errorBar, setErrorBar] = useState("");
  const [statisticalTest, setStatisticalTest] = useState("");
  const [chartPreview, setChartPreview] = useState("");
  const [chartId, setChartId] = useState("");
  const [chartWarnings, setChartWarnings] = useState<string[]>([]);
  const [chartDownloads, setChartDownloads] = useState<Record<string,string>>({});
  const [chartStyle, setChartStyle] = useState<ChartStyleState>({...defaultChartStyle});
  const [projectChartBlockers, setProjectChartBlockers] = useState<ChartBlocker[]>([]);
  const [privacyPreview, setPrivacyPreview] = useState<PrivacyPreview | null>(null);
  const [trashProjects, setTrashProjects] = useState<ProjectSummary[]>([]);
  const [modelBaseUrl, setModelBaseUrl] = useState("");
  const [modelName, setModelName] = useState("");
  const [modelProfileName, setModelProfileName] = useState("");
  const [modelKind, setModelKind] = useState<"local"|"online">("local");
  const [modelProfiles, setModelProfiles] = useState<ModelProfile[]>([]);
  const [apiKey, setApiKey] = useState("");
  const [projectsRoot, setProjectsRoot] = useState("");
  const [folderPickerBusy, setFolderPickerBusy] = useState(false);
  const [updateMessage, setUpdateMessage] = useState("");
  const [updateStatus, setUpdateStatus] = useState<UpdateStatus>({});
  const [updateChecking, setUpdateChecking] = useState(false);
  const [updateResult, setUpdateResult] = useState<UpdateCheckResult | null>(null);
  const [account, setAccount] = useState<LocalAccount | null>(null);
  const [accountReady, setAccountReady] = useState(false);
  const [loginUsername, setLoginUsername] = useState("attack");
  const [loginPassword, setLoginPassword] = useState("123456");
  const [loginBusy, setLoginBusy] = useState(false);
  const [loginError, setLoginError] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [passwordBusy, setPasswordBusy] = useState(false);
  const [annotationsEnabled, setAnnotationsEnabled] = useState(true);
  const [compositionTemplate, setCompositionTemplate] = useState<"1x2"|"2x2"|"hero_side"|"vertical">("2x2");
  const [compositionGap, setCompositionGap] = useState(8);
  const [compositionAspect, setCompositionAspect] = useState<"auto"|"1:1"|"4:3"|"16:9">("auto");
  const [compositionWidth, setCompositionWidth] = useState<89|180>(180);
  const [compositionDpi, setCompositionDpi] = useState<300|600>(600);
  const [panelOrder, setPanelOrder] = useState<string[]>([]);
  const [panelZoom, setPanelZoom] = useState<Record<string,number>>({});
  const [compositionPreview, setCompositionPreview] = useState("");
  const [compositionDownloads, setCompositionDownloads] = useState<Record<string,string>>({});
  const [compositionWarnings, setCompositionWarnings] = useState<string[]>([]);
  const [compositionSaving, setCompositionSaving] = useState(false);
  const [compositionCaption, setCompositionCaption] = useState("");
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [assistantDirty, setAssistantDirty] = useState(false);
  const allowProjectChange = () => {
    if(assistantDirty && !window.confirm("工作稿有未保存编辑。确认放弃这些编辑并切换项目？"))return false;
    if(paperDirty && !window.confirm("Paper 有未保存草稿，是否继续切换工作台项目？Paper 草稿仍会保留，退出前请保存。"))return false;
    return true;
  };
  const [outlineOpen,setOutlineOpen]=useState(true);
  const [readerOpen,setReaderOpen]=useState(false);
  const [assistantSelection,setAssistantSelection]=useState("");
  const [topic,setTopic]=useState("");
  const [topicBusy,setTopicBusy]=useState(false);
  const [citationChecks,setCitationChecks]=useState<Record<string,string>>({});
  const [sourceListOpen, setSourceListOpen] = useState(false);
  const [bulkRejectOpen, setBulkRejectOpen] = useState(false);
  const [bulkRejectBusy, setBulkRejectBusy] = useState(false);
  const [editingCandidate, setEditingCandidate] = useState<Candidate | null>(null);
  const [sourceDraft, setSourceDraft] = useState<{title:string;sourceUrl:string;author:string;license:string;licenseUrl:string;reuseStatus:CandidateReuseStatus;verified:boolean;reason:string}>({title:"",sourceUrl:"",author:"",license:"",licenseUrl:"",reuseStatus:"unknown",verified:false,reason:""});
  const [documentEditorOpen, setDocumentEditorOpen] = useState(false);
  const [documentDrafts, setDocumentDrafts] = useState<AnchorRecord[]>([]);
  const [documentFullText, setDocumentFullText] = useState("");
  const [documentEditReason, setDocumentEditReason] = useState("");
  const [documentFind, setDocumentFind] = useState("");
  const [documentReplace, setDocumentReplace] = useState("");
  const [documentFindCursor, setDocumentFindCursor] = useState(-1);
  const [documentSaving, setDocumentSaving] = useState(false);
  const [exportOverrideReason, setExportOverrideReason] = useState("");
  const [exporting, setExporting] = useState(false);
  const [jobsOpen, setJobsOpen] = useState(false);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [jobsLoading, setJobsLoading] = useState(false);
  const [ocrConfirming, setOcrConfirming] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const datasetInput = useRef<HTMLInputElement>(null);
  const packageInput = useRef<HTMLInputElement>(null);
  const fullDocumentTextareaRef = useRef<HTMLTextAreaElement>(null);

  const refreshProtectedData = async () => {
    const [projectResponse,trashResponse,healthResponse,modelsResponse,updateResponse] = await Promise.all([
      fetch("/api/projects", {credentials:"include"}),
      fetch("/api/projects?include_trash=true", {credentials:"include"}),
      fetch("/api/health"),
      fetch("/api/settings/models", {credentials:"include"}),
      fetch("/api/settings/update", {credentials:"include"}),
    ]);
    const [projectItems,allProjectItems,health,models,update] = await Promise.all([
      projectResponse.ok ? projectResponse.json() : [],
      trashResponse.ok ? trashResponse.json() : [],
      healthResponse.ok ? healthResponse.json() : {},
      modelsResponse.ok ? modelsResponse.json() : [],
      updateResponse.ok ? updateResponse.json() : {},
    ]);
    setApiReady(projectResponse.ok); setRuntimeHealth(health); setProjects(projectItems); setTrashProjects(allProjectItems.filter((item:ProjectSummary)=>item.trashed_at)); setProjectsRoot((health as RuntimeHealth).projects_root || "");
    setModelProfiles(Array.isArray(models)?models:[]); setUpdateStatus(update); setUpdateMessage((update as {message?:string}).message || "");
  };

  useEffect(() => {
    let active = true;
    const initialize = async () => {
      try {
        const session = await fetch("/api/session", {credentials:"include"});
        if (!session.ok) throw new Error("Local session unavailable");
        const statusResponse = await fetch("/api/account/status", {credentials:"include"});
        const status = statusResponse.ok ? await statusResponse.json() : {authenticated:false};
        if (!active) return;
        if (!status.authenticated || !status.account) { setApiReady(true); setAccountReady(true); return; }
        const signedIn:LocalAccount = status.account;
        setAccount(signedIn);
        if (typeof signedIn.preferences.annotations_enabled === "boolean") {
          setAnnotationsEnabled(signedIn.preferences.annotations_enabled);
          window.localStorage.setItem("nature-workbench-annotations", signedIn.preferences.annotations_enabled ? "on" : "off");
        }
        if (signedIn.preferences.last_screen && signedIn.preferences.last_screen !== "workspace") setScreen(signedIn.preferences.last_screen);
        await refreshProtectedData();
        if (active) setAccountReady(true);
      } catch {
        if (active) { setApiReady(false); setAccountReady(true); }
      }
    };
    initialize();
    return () => { active = false; };
  }, [setScreen]);
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const saved = window.localStorage.getItem("nature-workbench-annotations");
      if (saved === "off") setAnnotationsEnabled(false);
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(null), 2600); return () => window.clearTimeout(timer); }, [toast]);

  const activeDatasetProfile = useMemo(
    () => dataset?.sheets?.find(sheet=>sheet.name===sheetName) || dataset,
    [dataset,sheetName],
  );
  const displayParagraphs = anchors.length ? anchors.map(anchor => anchor.text) : currentProject ? [] : demoParagraphs;
  const mode = workspaceMode(currentProject,anchors.length,dataset?1:0);
  const dataOnlyProject = mode === "data";
  const sourceIssues = sourceEditIssues(sourceDraft);
  const selectedAnchor = anchors[selectedParagraph] ?? null;
  const currentProjectId = currentProject?.id ?? null;
  const selectedAnchorId = selectedAnchor?.id ?? null;
  useEffect(() => {
    if (!currentProjectId) return;
    let active = true;
    const anchorId = selectedAnchorId;
    const loadAnchorCandidates = async () => {
      try {
        const response = await fetch(anchorId ? `/api/anchors/${anchorId}/candidates` : `/api/projects/${currentProjectId}/literature`, {credentials:"include"});
        if (!active) return;
        if (!response.ok) throw new Error("候选读取失败");
        const records:CandidateApiRecord[] = await response.json();
        setCandidates(records.map(candidateFromApi));
      } catch {
        if (active) {
          setCandidates([]);
          setToast("该段落的来源候选读取失败，可稍后重试");
        }
      }
    };
    loadAnchorCandidates();
    return () => { active = false; };
  }, [currentProjectId, selectedAnchorId]);
  const sections = useMemo(() => {
    const counts = new Map<string, number>();
    anchors.forEach(anchor => counts.set(anchor.section || "未命名章节", (counts.get(anchor.section || "未命名章节") || 0) + 1));
    return Array.from(counts.entries());
  }, [anchors]);
  const acceptedCount = useMemo(() => candidates.filter(c => c.status === "accepted").length, [candidates]);
  const pendingRejectCount = useMemo(() => candidates.filter(candidate=>candidate.status==="pending" && !candidate.citationSelected).length, [candidates]);
  const rejectedCandidateCount = useMemo(() => candidates.filter(candidate=>candidate.status==="rejected").length, [candidates]);
  const unresolvedLicenseCount = useMemo(
    () => candidates.filter(candidate=>candidate.status==="pending" && !!candidate.previewUrl && candidate.reuse!=="safe").length,
    [candidates],
  );
  const enabledModel = useMemo(() => modelProfiles.find(profile=>profile.enabled) || null, [modelProfiles]);
  const compositionItems = useMemo(() => panelOrder.map(key=>{
    if (key.startsWith("asset:")) {
      const id=key.slice(6); const candidate=candidates.find(item=>item.id===id);
      return candidate ? {key,kind:"asset" as const,id,title:candidate.title,previewUrl:candidate.previewUrl || null} : null;
    }
    if (key.startsWith("chart:") && chartId && key===`chart:${chartId}`) return {key,kind:"chart" as const,id:chartId,title:chartTitle.trim() || "数据图",previewUrl:chartPreview || null};
    return null;
  }).filter(Boolean) as CompositionPanel[], [panelOrder,candidates,chartId,chartTitle,chartPreview]);
  const documentParagraphTexts = useMemo(() => documentFullText.split(/\n\s*\n/).map(text=>text.trim()), [documentFullText]);
  const documentStructureValid = documentParagraphTexts.length === documentDrafts.length && documentParagraphTexts.every(Boolean);
  const changedDocumentCount = useMemo(() => documentStructureValid ? documentParagraphTexts.filter((text,index)=>documentDrafts[index]?.text!==text).length : 0, [documentParagraphTexts,documentDrafts,documentStructureValid]);
  const documentCharacterCount = documentFullText.length;
  const documentFindMatches = useMemo(() => {
    if (!documentFind) return [] as Array<{start:number;end:number}>;
    const matches:Array<{start:number;end:number}> = [];
    let start = 0;
    while ((start = documentFullText.indexOf(documentFind, start)) >= 0) {
      matches.push({start,end:start+documentFind.length});
      start += Math.max(1, documentFind.length);
    }
    return matches;
  }, [documentFullText,documentFind]);
  const rejectPendingCandidates = async () => {
    const pendingIds = candidates.filter(candidate=>candidate.status==="pending" && !candidate.citationSelected).map(candidate=>candidate.id);
    if (!pendingIds.length) { setBulkRejectOpen(false); setToast("当前没有待处理候选"); return; }
    setBulkRejectBusy(true);
    try {
      if (currentProject && selectedAnchor && pendingIds.some(id=>!isDemoCandidateId(id))) {
        const response = await fetch(`/api/projects/${currentProject.id}/anchors/${selectedAnchor.id}/candidates/reject-pending`, {
          method:"POST", credentials:"include", headers:{"content-type":"application/json"},
          body:JSON.stringify({reason:"用户在工作台批量拒绝未选择候选"}),
        });
        if (!response.ok) throw new Error("批量决策未保存");
        const result:{rejected_ids:string[];rejected_count:number} = await response.json();
        const rejected = new Set(result.rejected_ids);
        setCandidates(items=>items.map(item=>rejected.has(item.id)?{...item,status:"rejected"}:item));
        setPanelOrder(items=>items.filter(item=>!rejected.has(item.replace(/^asset:/,""))));
        setToast(`已批量拒绝 ${result.rejected_count} 个候选；已接受素材保持不变`);
      } else {
        const rejected = new Set(pendingIds);
        setCandidates(items=>items.map(item=>rejected.has(item.id)?{...item,status:"rejected"}:item));
        setPanelOrder(items=>items.filter(item=>!rejected.has(item.replace(/^asset:/,""))));
        setToast(`已批量拒绝 ${pendingIds.length} 个示例候选`);
      }
      setBulkRejectOpen(false);
    } catch (error) {
      setToast(error instanceof Error ? error.message : "批量拒绝失败");
    } finally {
      setBulkRejectBusy(false);
    }
  };

  const openDemo = () => { if(!allowProjectChange())return;setAssistantDirty(false); setCurrentProject(null); setAnchors([]); setCandidates(demoCandidates); setSelectedParagraph(1); setQuery("城市热岛 绿色基础设施 空间分布"); setScreen("workspace"); setToast("已打开示例项目；上传真实文档后将使用本地解析服务"); };
  const selectDocumentParagraph = (index:number, revealAssets=false) => {
    setCandidates([]);
    setSelectedParagraph(index);
    setQuery("");
    if (revealAssets) {
      setActiveTab("assets");
      setInspectorOpen(true);
    }
  };
  const loadProject = async (projectId: string, discardConfirmed = false) => {
    if(projectId!==currentProject?.id && !discardConfirmed && !allowProjectChange())return null;
    if(projectId!==currentProject?.id)setAssistantDirty(false);
    const [projectResponse,datasetsResponse,chartsResponse,compositionsResponse] = await Promise.all([
      fetch(`/api/projects/${projectId}`, {credentials:"include"}),
      fetch(`/api/projects/${projectId}/datasets`, {credentials:"include"}),
      fetch(`/api/projects/${projectId}/charts`, {credentials:"include"}),
      fetch(`/api/projects/${projectId}/compositions`, {credentials:"include"}),
    ]);
    if (!projectResponse.ok) throw new Error("项目读取失败");
    const project = await projectResponse.json();
    const restoredDatasets:DatasetProfile[] = datasetsResponse.ok ? await datasetsResponse.json() : [];
    const restoredCharts:ChartRecord[] = chartsResponse.ok ? await chartsResponse.json() : [];
    const restoredCompositions:CompositionRecord[] = compositionsResponse.ok ? await compositionsResponse.json() : [];
    const restoredChart = restoredCharts[0] || null;
    const restoredComposition = restoredCompositions[0] || null;
    const restoredAnchorId = restoredComposition?.anchor_id || restoredChart?.anchor_id || project.anchors?.[0]?.id;
    const candidateResponse = await fetch(restoredAnchorId ? `/api/anchors/${restoredAnchorId}/candidates` : `/api/projects/${projectId}/literature`, {credentials:"include"});
    const restoredCandidates:CandidateApiRecord[] = candidateResponse?.ok ? await candidateResponse.json() : [];
    const restoredDataset = (restoredChart && restoredDatasets.find(item=>item.id===restoredChart.dataset_id)) || restoredDatasets[0] || null;
    const restoredSheetName = restoredChart?.sheet_name || restoredDataset?.selected_sheet || restoredDataset?.sheets?.[0]?.name || "";
    const restoredDatasetProfile = restoredDataset?.sheets?.find(sheet=>sheet.name===restoredSheetName) || restoredDataset;
    const anchorIndex = Math.max(0,(project.anchors || []).findIndex((anchor:AnchorRecord)=>anchor.id===restoredAnchorId));

    setCurrentProject(project); setAnchors(project.anchors || []); setSelectedParagraph(anchorIndex); setCandidates(restoredCandidates.map(candidateFromApi)); setQuery(""); setDataset(restoredDataset); setSheetName(restoredSheetName);
    setProjectChartBlockers(restoredCharts.filter(chart=>(chart.warnings || []).length>0).map(chart=>({id:chart.id,title:chart.title || `${chart.y} by ${chart.x}`,warnings:chart.warnings || []})));
    if (restoredChart) {
      setChartId(restoredChart.id); setChartPreview(`/api${restoredChart.preview_url}`); setChartWarnings(restoredChart.warnings || []);
      setChartDownloads(Object.fromEntries(Object.entries(restoredChart.download_urls || {}).map(([format,url])=>[format,`/api${url}`])));
      setChartType(restoredChart.chart_type); setXField(restoredChart.x); setYField(restoredChart.y); setGroupField(restoredChart.group || "");
      setChartTitle(restoredChart.title || ""); setIndependentUnit(restoredChart.independent_unit || "");
      setErrorBar(normalizedErrorBar(restoredChart.error_bar)); setStatisticalTest(restoredChart.statistical_test || "");
      setChartStyle({
        widthMm:restoredChart.width_mm ?? 180, heightMm:restoredChart.height_mm ?? 110,
        fontFamily:restoredChart.font_family || "Arial", fontSizePt:restoredChart.font_size_pt ?? 8, titleSizePt:restoredChart.title_size_pt ?? 11,
        xLabel:restoredChart.x_label || "", yLabel:restoredChart.y_label || "", gridMode:restoredChart.grid_mode || "none",
        xScale:restoredChart.x_scale || "linear", yScale:restoredChart.y_scale || "linear",
        xMin:restoredChart.x_min === null || restoredChart.x_min === undefined ? "" : String(restoredChart.x_min),
        xMax:restoredChart.x_max === null || restoredChart.x_max === undefined ? "" : String(restoredChart.x_max),
        yMin:restoredChart.y_min === null || restoredChart.y_min === undefined ? "" : String(restoredChart.y_min),
        yMax:restoredChart.y_max === null || restoredChart.y_max === undefined ? "" : String(restoredChart.y_max),
        tickCount:restoredChart.tick_count ?? 6, showMinorTicks:restoredChart.show_minor_ticks ?? false,
        legendPosition:restoredChart.legend_position || "auto", palette:restoredChart.palette || "nature",
        lineWidth:restoredChart.line_width ?? 1.5, marker:restoredChart.marker || "auto", showDataLabels:restoredChart.show_data_labels ?? false,
      });
    } else {
      const axes = restoredDatasetProfile ? defaultDatasetAxes(restoredDatasetProfile) : {x:"",y:""};
      setChartId(""); setChartPreview(""); setChartWarnings([]); setChartDownloads({}); setChartStyle({...defaultChartStyle}); setChartType(restoredDatasetProfile?.recommended_charts?.[0] || "grouped_bar");
      setXField(axes.x); setYField(axes.y); setGroupField(""); setChartTitle(""); setIndependentUnit(""); setErrorBar(""); setStatisticalTest("");
    }
    if (restoredComposition) {
      setCompositionPreview(`/api${restoredComposition.preview_url}`);
      setCompositionDownloads(Object.fromEntries(Object.entries(restoredComposition.download_urls || {}).map(([format,url])=>[format,`/api${url}`])));
      setCompositionWarnings(restoredComposition.warnings || []); setCompositionCaption(restoredComposition.caption || "");
      setCompositionTemplate(restoredComposition.template); setCompositionGap(restoredComposition.gap_px); setCompositionAspect(restoredComposition.aspect_ratio);
      setCompositionWidth(restoredComposition.width_mm); setCompositionDpi(restoredComposition.dpi);
      setPanelOrder((restoredComposition.panels || []).map(panel=>`${panel.kind}:${panel.source_id}`));
      setPanelZoom(Object.fromEntries((restoredComposition.panels || []).map(panel=>[`${panel.kind}:${panel.source_id}`,panel.crop_zoom || 1])));
      setActiveTab("compose");
    } else {
      setCompositionPreview(""); setCompositionDownloads({}); setCompositionWarnings([]); setCompositionCaption(restoredChart?.title || "");
      setCompositionTemplate("2x2"); setCompositionGap(8); setCompositionAspect("auto"); setCompositionWidth(180); setCompositionDpi(600);
      setPanelOrder(restoredChart ? [`chart:${restoredChart.id}`] : []); setPanelZoom({}); setActiveTab(restoredChart ? "chart" : "assets");
    }
    setScreen("workspace");
    if (restoredDataset || restoredChart || restoredComposition) setToast(`已恢复项目工作状态：${restoredDatasets.length} 个数据集、${restoredCharts.length} 张图表、${restoredCompositions.length} 个组图`);
    if (!(project.anchors || []).length) setToast(!project.documents?.length && restoredDataset ? `已恢复纯数据项目：${restoredDatasets.length} 个数据集、${restoredCharts.length} 张图表` : project.documents?.length ? "原稿已保存，请查看文档解析状态" : project.status==="选题与写作" ? "已恢复选题项目，可继续检索文献和编辑工作稿" : "该项目尚未成功上传文件，请重新选择");
    return project;
  };
  const waitForAnalysis = async (projectId: string, jobId: string) => {
    for (let attempt = 0; attempt < 80; attempt += 1) {
      const jobs = await fetch(`/api/projects/${projectId}/jobs`, {credentials:"include"}).then(r=>r.json());
      const job = jobs.find((item:{id:string}) => item.id === jobId);
      if (job?.state === "completed") { const project = await loadProject(projectId,true); setToast(job.message || "文档解析完成"); return project; }
      if (job?.state === "failed") { await loadProject(projectId,true); throw new Error(job.message || "文档解析失败"); }
      await new Promise(resolve => window.setTimeout(resolve, 600));
    }
    await loadProject(projectId,true);
    throw new Error("解析仍在后台运行，可稍后刷新状态；原稿已保存");
  };
  const appendMaterials = async (files:File[]) => {
    const projectId=currentProject?.id;
    if(!projectId || uploadBusy)return;
    setUploadBusy(true);
    const failures:string[]=[];
    let hasDataset=!!dataset;
    for(const file of files){
      try{
        const body=new FormData();body.append("file",file);
        const datasetFile=/\.(csv|xlsx)$/i.test(file.name);
        const uploaded=await fetch(`/api/projects/${projectId}/${datasetFile?"datasets":"documents?role=supplement"}`,{method:"POST",credentials:"include",body});
        if(!uploaded.ok){const detail=await uploaded.json().catch(()=>({}));throw new Error(typeof detail.detail==="string"?detail.detail:`HTTP ${uploaded.status}`);}
        const record=await uploaded.json();
        if(datasetFile && activeProjectRef.current===projectId){
          setMaterialDatasets(rows=>[...rows.filter(row=>row.id!==record.id),record]);
          if(!hasDataset){
            const profile=record as DatasetProfile;
            const sheet=profile.selected_sheet||profile.sheets?.[0]?.name||"";
            const selected=profile.sheets?.find(row=>row.name===sheet)||profile;
            const axes=defaultDatasetAxes(selected);
            setDataset(profile);setSheetName(sheet);setXField(axes.x);setYField(axes.y);setActiveTab("chart");hasDataset=true;
          }
        }
        if(!datasetFile){
          const analysis=await academicRequest(`/api/projects/${projectId}/analyze`,{document_id:record.id});
          for(let attempt=0;attempt<80;attempt++){
            const jobs=await academicRequest(`/api/projects/${projectId}/jobs`);
            const job=jobs.find((item:JobRecord)=>item.id===analysis.job_id);
            if(job?.state==="completed")break;
            if(job?.state==="failed")throw new Error(job.message||"解析失败，原文件已保留");
            if(attempt===79)throw new Error("解析仍在后台进行，请稍后查看");
            await new Promise(resolve=>window.setTimeout(resolve,600));
          }
        }
      }catch(error){failures.push(`${file.name}：${error instanceof Error?error.message:"上传失败"}`);}
    }
    try{
      if(activeProjectRef.current===projectId){
        const latest=await academicRequest(`/api/projects/${projectId}`);
        if(activeProjectRef.current===projectId)setCurrentProject(latest);
      }
      setToast(failures.length?failures.join("；"):"材料已添加到当前项目，可在“材料”中查看");
    }catch(error){setToast(error instanceof Error?error.message:"材料列表刷新失败");}
    finally{setUploadBusy(false);}
  };
  const onFiles = async (files: FileList | null) => {
    if(files?.length && screen==="workspace" && currentProject){await appendMaterials(Array.from(files));return;}
    if (!files?.length || uploadBusy || !allowProjectChange()) return;
    setAssistantDirty(false);
    const file = files[0];
    if (!apiReady) { setToast("本地服务尚未连接，请稍后重试上传"); return; }
    setUploadBusy(true);
    try {
      const projectResponse = await fetch("/api/projects", { method: "POST", credentials: "include", headers: {"content-type":"application/json"}, body: JSON.stringify({name: file.name.replace(/\.[^.]+$/, ""), quality_mode: "quality"}) });
      if (!projectResponse.ok) throw new Error("项目创建失败，请检查本机账户和服务");
      const project = await projectResponse.json();
      const body = new FormData(); body.append("file", file);
      const isDataset = /\.(csv|xlsx)$/i.test(file.name);
      const uploadResponse = await fetch(`/api/projects/${project.id}/${isDataset ? "datasets" : "documents?role=manuscript"}`, {method:"POST",credentials:"include",body});
      if (!uploadResponse.ok) {
        const error=await uploadResponse.json().catch(()=>({}));
        throw new Error(`上传未完成（HTTP ${uploadResponse.status}）：${typeof error.detail==="string"?error.detail:"请检查文件格式与本地服务"}`);
      }
      const uploaded = await uploadResponse.json();
      setAnchors([]); setCandidates([]); setDataset(null); setChartPreview(""); setChartId(""); setChartDownloads({}); setPanelOrder([]);
      setCurrentProject({...project,documents:isDataset?[]:[{...uploaded,parser:"pending"}],status:isDataset?project.status:"等待解析"}); setScreen("workspace");
      if (isDataset) {
        const profile:DatasetProfile = uploaded; const initialSheet=profile.selected_sheet || profile.sheets?.[0]?.name || ""; const initialProfile=profile.sheets?.find(sheet=>sheet.name===initialSheet) || profile; setDataset(profile); setSheetName(initialSheet); setActiveTab("chart");
        const axes = defaultDatasetAxes(initialProfile);
        setXField(axes.x); setYField(axes.y); setToast("数据已分析，可生成科研图");
      } else {
        const analysisResponse = await fetch(`/api/projects/${project.id}/analyze`, {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({document_id:uploaded.id})});
        if (!analysisResponse.ok) throw new Error("原稿已保存，但解析任务未能启动；可点击重新解析");
        const analysis = await analysisResponse.json(); setToast("文档已安全保存，正在本地解析");
        await waitForAnalysis(project.id, analysis.job_id);
      }
      const refreshed = await fetch("/api/projects", {credentials:"include"}).then(r=>r.json()); setProjects(refreshed);
    } catch (error) { setToast(error instanceof Error && error.message ? error.message : "上传未完成，请检查文件格式与本地服务"); } finally { setUploadBusy(false); }
  };

  const retryDocument = async () => {
    if (!currentProject || uploadBusy) return;
    setUploadBusy(true);
    try {
      if (mode==="document_pending") { await loadProject(currentProject.id); return; }
      const retryTarget=currentProject.documents?.find(doc=>doc.parser==="pending"||doc.parser==="unknown") || currentProject.documents?.[currentProject.documents.length-1];
      if(!retryTarget)throw new Error("请先添加材料");
      const response=await fetch(`/api/projects/${currentProject.id}/analyze`,{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({document_id:retryTarget.id})});
      if (!response.ok) throw new Error("解析任务未能启动");
      const result=await response.json(); await waitForAnalysis(currentProject.id,result.job_id);
    } catch(error) { setToast(error instanceof Error?error.message:"读取失败"); }
    finally { setUploadBusy(false); }
  };
  const setCitationStatus = async (id:string, selected:boolean) => {
    if(!currentProject || isDemoCandidateId(id))return false;
    const projectId=currentProject.id;
    let decisionSaved=false;
    const request=++projectionRequestRef.current;
    setCitationSaving(id);
    try{
      if(selectedAnchor){
        await publishParagraphCitation({
          projectId,anchorId:selectedAnchor.id,referenceId:id,selected,request:academicRequest,
          isCurrent:()=>activeProjectRef.current===projectId && request===projectionRequestRef.current,
          onProjection:projection=>{setCitationProjection(projection);setCitationError("");},
        });
        decisionSaved=true;
      }else{
        await academicRequest("/api/decisions",{project_id:projectId,target_type:"citation",target_id:id,action:selected?"accept":"reject",reason:"用户更新项目收藏",warnings:[]});
        decisionSaved=true;
        await refreshCitationProjection(projectId);
      }
      if(activeProjectRef.current===projectId)setCandidates(items=>items.map(item=>item.id===id?{...item,citationSelected:selected}:item));
      setToast(selected?(selectedAnchor?"已引用到当前段落":"已收藏；选择正文段落后可引用"):"已取消此处引用");return true;
    }catch(error){setToast(decisionSaved?"引用状态已保存，编号刷新失败，请稍后重试":error instanceof Error?error.message:"引用保存失败");return false;}
    finally{setCitationSaving(null);}
  };
  const selectMaterialDataset=(profile:DatasetProfile)=>{
    if(!allowProjectChange())return;
    const sheet=profile.selected_sheet||profile.sheets?.[0]?.name||"";
    const selected=profile.sheets?.find(row=>row.name===sheet)||profile;
    const axes=defaultDatasetAxes(selected);
    setDataset(profile);setSheetName(sheet);setXField(axes.x);setYField(axes.y);setGroupField("");setActiveTab("chart");
    setChartId("");setChartPreview("");setChartDownloads({});setChartWarnings([]);
  };
  const selectManuscript = async(documentId:string)=>{
    if(!currentProject || !allowProjectChange())return;
    try{
      const response=await fetch(`/api/projects/${currentProject.id}/manuscript`,{method:"PUT",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({document_id:documentId})});
      if(!response.ok)throw new Error("正文选择未保存，请检查材料解析状态");
      projectionRequestRef.current++;
      await loadProject(currentProject.id,true);
    }catch(error){setToast(error instanceof Error?error.message:"正文选择失败");}
  };
  const addReference = async () => {
    if (!currentProject || referenceSaving) return;
    setReferenceSaving(true);
    try {
      const response=await fetch(selectedAnchor ? `/api/anchors/${selectedAnchor.id}/references` : `/api/projects/${currentProject.id}/references`,{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({title:referenceDraft.title,source_url:referenceDraft.sourceUrl,author:referenceDraft.author||null})});
      const result=await response.json();
      if (!response.ok) throw new Error(typeof result.detail==="string"?result.detail:"请填写标题及完整来源网址");
      setCandidates(items=>[candidateFromApi(result),...items]);setReferenceOpen(false);setReferenceDraft({title:"",sourceUrl:"",author:""});setToast(selectedAnchor?"已引用到当前段落":"已收藏文献");await refreshCitationProjection(currentProject.id);
    }catch(error){setToast(error instanceof Error?error.message:"引用未保存");}
    finally{setReferenceSaving(false);}
  };
  const searchAssets = async () => {
    if (!currentProject) { setToast("示例模式不会发起网络检索；请创建项目"); return; }
    setToast("正在检索学术文献…");
    try {
      const response = await fetch(selectedAnchor ? `/api/anchors/${selectedAnchor.id}/literature` : `/api/projects/${currentProject.id}/literature`, {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({query})});
      if (!response.ok) {
        const failure = await response.json().catch(()=>({}));
        const detail = failure.detail;
        throw new Error(typeof detail === "string" ? detail : detail?.message || "文献检索失败或网络不可用");
      }
      const items = await response.json();
      setCandidates(items.map((item:CandidateApiRecord) => candidateFromApi(item)));
      const warning = response.headers.get("X-NFW-Search-Warning");
      setToast(warning ? `找到 ${items.length} 个来源；部分来源暂不可用：${warning}` : `找到 ${items.length} 篇文献，请核对后引用`);
    } catch (error) { setToast(error instanceof Error ? error.message : "检索失败"); }
  };

  const suggestLocalQuery = async () => {
    if (!selectedAnchor) { setQuery(currentProject?.name||topic);setToast("已使用项目选题作为检索词，尚未联网"); return; }
    const response = await fetch(`/api/anchors/${selectedAnchor.id}/local-search-terms`, {credentials:"include"});
    if (!response.ok) { setToast("本地检索词生成失败"); return; }
    const result = await response.json(); setQuery(result.query || selectedAnchor.section); setToast("已由本地规则生成检索词；正文未发送给 AI，也尚未联网");
  };

  const uploadDataset = async (files: FileList | null) => {
    if (!files?.length || !currentProject) return;
    const body = new FormData(); body.append("file", files[0]);
    const response = await fetch(`/api/projects/${currentProject.id}/datasets`, {method:"POST",credentials:"include",body});
    if (!response.ok) { setToast("数据文件上传失败"); return; }
    const profile:DatasetProfile = await response.json(); const initialSheet=profile.selected_sheet || profile.sheets?.[0]?.name || ""; const initialProfile=profile.sheets?.find(sheet=>sheet.name===initialSheet) || profile; setDataset(profile); setSheetName(initialSheet);
    const axes = defaultDatasetAxes(initialProfile);
    setXField(axes.x); setYField(axes.y); setChartType(initialProfile.recommended_charts?.[0] || "grouped_bar"); setToast(`已分析 ${initialProfile.rows} 行数据${initialSheet ? ` · 工作表 ${initialSheet}` : ""}`);
  };

  const selectDatasetSheet = (value:string) => {
    const profile = dataset?.sheets?.find(sheet=>sheet.name===value);
    if (!profile) return;
    const axes = defaultDatasetAxes(profile);
    setSheetName(value); setXField(axes.x); setYField(axes.y); setGroupField("");
    setChartType(profile.recommended_charts?.[0] || "grouped_bar");
    setChartId(""); setChartPreview(""); setChartDownloads({}); setChartWarnings([]);
    setToast(`已切换到工作表“${value}” · ${profile.rows} 行；请核对字段后重新制图`);
  };

  const waitForPersistentJob = async (jobId:string) => {
    for (let attempt=0; attempt<240; attempt+=1) {
      const response = await fetch(`/api/jobs/${jobId}`, {credentials:"include"});
      if (!response.ok) throw new Error("任务状态读取失败");
      const job:JobRecord = await response.json();
      if (job.state === "completed") return job.result || {};
      if (job.state === "failed" || job.state === "cancelled") throw new Error(job.message || "后台任务未完成");
      await new Promise(resolve=>window.setTimeout(resolve,500));
    }
    throw new Error("任务仍在后台运行，可在任务中心继续查看");
  };

  const createChart = async () => {
    if (!currentProject || !dataset || !xField || !yField) { setToast("请先上传数据并选择 X/Y 字段"); return; }
    const optionalNumber=(value:string)=>value.trim()===""?null:Number(value);
    const response = await fetch(`/api/projects/${currentProject.id}/chart-jobs`, {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({dataset_id:dataset.id,sheet_name:sheetName || null,anchor_id:selectedAnchor?.id || null,chart_type:chartType,x:xField,y:yField,group:groupField || null,title:chartTitle.trim() || `${yField} by ${xField}`,independent_unit:independentUnit.trim() || null,error_bar:errorBar || null,statistical_test:statisticalTest.trim() || null,width_mm:chartStyle.widthMm,height_mm:chartStyle.heightMm,font_family:chartStyle.fontFamily,font_size_pt:chartStyle.fontSizePt,title_size_pt:chartStyle.titleSizePt,x_label:chartStyle.xLabel.trim() || null,y_label:chartStyle.yLabel.trim() || null,grid_mode:chartStyle.gridMode,x_scale:chartStyle.xScale,y_scale:chartStyle.yScale,x_min:optionalNumber(chartStyle.xMin),x_max:optionalNumber(chartStyle.xMax),y_min:optionalNumber(chartStyle.yMin),y_max:optionalNumber(chartStyle.yMax),tick_count:chartStyle.tickCount,show_minor_ticks:chartStyle.showMinorTicks,legend_position:chartStyle.legendPosition,palette:chartStyle.palette,line_width:chartStyle.lineWidth,marker:chartStyle.marker,show_data_labels:chartStyle.showDataLabels})});
    if (!response.ok) { const detail=await response.json().catch(()=>({})); setToast(detail.detail || "制图失败，请检查字段类型与坐标范围"); return; }
    setToast("科研图已进入后台队列，可在任务中心暂停或恢复");
    try { const queued=await response.json(); const chart=await waitForPersistentJob(queued.job_id) as {id:string;preview_url:string;download_urls:Record<string,string>;warnings:string[]}; const resolvedTitle=chartTitle.trim() || `${yField} by ${xField}`; setChartId(chart.id); setChartPreview(`/api${chart.preview_url}`); setChartDownloads(Object.fromEntries(Object.entries(chart.download_urls || {}).map(([format,url])=>[format,`/api${url}`]))); setChartWarnings(chart.warnings || []); setProjectChartBlockers(items=>[...items.filter(item=>item.id!==chart.id),...(chart.warnings?.length?[{id:chart.id,title:resolvedTitle,warnings:chart.warnings}]:[])]); setPanelOrder(items=>[...items.filter(item=>!item.startsWith("chart:")),`chart:${chart.id}`]); setCompositionCaption(current=>current || resolvedTitle); setToast(chart.warnings.length ? `草图已生成并加入组图；${chart.warnings.length} 项统计信息待确认` : "投稿统计说明（导出前填写）已完整，图表已加入组图"); } catch(error) { setToast(error instanceof Error?error.message:"制图后台任务失败"); }
  };

  const openPrivacyPreview = async (textOverride?: string) => {
    const text = textOverride ?? displayParagraphs[selectedParagraph] ?? "";
    const response = await fetch("/api/privacy/preview", {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({text})});
    if (response.ok) setPrivacyPreview(await response.json());
    setPrivacyOpen(true);
  };

  const exportProject = async (overrideStatistics = false) => {
    if (!currentProject) { setExportOpen(false); setToast("示例模式不生成文件"); return; }
    if (overrideStatistics && exportOverrideReason.trim().length < 5) { setToast("强制导出前请填写至少 5 个字的风险确认理由"); return; }
    setExporting(true);
    try {
      const response = await fetch(`/api/projects/${currentProject.id}/export-jobs`, {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({override_statistics:overrideStatistics,override_reason:overrideStatistics ? exportOverrideReason.trim() : null})});
      if (!response.ok) { setToast("导出任务创建失败，请检查项目状态"); return; }
      setToast("导出已进入后台队列；关闭页面也不会丢失任务");
      const queued=await response.json();
      try { const manifest=await waitForPersistentJob(queued.job_id) as {download_url:string}; setExportOpen(false); setExportOverrideReason(""); window.location.assign(`/api${manifest.download_url}`); setToast(overrideStatistics ? "带风险覆盖记录的审计包已生成" : "投稿级审计导出包已生成"); }
      catch(error) { const message=error instanceof Error?error.message:"导出失败"; setToast(message.includes("statistics_confirmation_required")?"安全导出已阻止：请补全统计信息，或填写理由后强制导出":message); }
    } finally { setExporting(false); }
  };

  const showLibrary = async () => {
    const items = await fetch("/api/projects", {credentials:"include"}).then(r=>r.ok?r.json():[]);
    setProjects(items); setScreen("library");
  };
  const showTrash = async () => {
    const items = await fetch("/api/projects?include_trash=true", {credentials:"include"}).then(r=>r.ok?r.json():[]);
    setTrashProjects(items.filter((item:ProjectSummary)=>item.trashed_at)); setScreen("trash");
  };
  const trashProject = async (projectId:string) => {
    const response = await fetch(`/api/projects/${projectId}/trash`, {method:"POST",credentials:"include"});
    if (response.ok) { setProjects(items=>items.filter(item=>item.id!==projectId)); setToast("项目已移入 30 天回收站"); }
  };
  const restoreProject = async (projectId:string) => {
    const response = await fetch(`/api/projects/${projectId}/restore`, {method:"POST",credentials:"include"});
    if (response.ok) { setTrashProjects(items=>items.filter(item=>item.id!==projectId)); setToast("项目已恢复"); }
  };
  const importProjectPackage = async (event:ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".zip")) { setToast("请选择 .nfw-project.zip 便携项目包"); return; }
    const body = new FormData(); body.append("file", file);
    const response = await fetch("/api/projects/import", {method:"POST",credentials:"include",body});
    const result = await response.json().catch(()=>({}));
    if (!response.ok) { setToast(result.detail || "项目包导入失败；请检查文件完整性或项目是否已存在"); return; }
    const items = await fetch("/api/projects", {credentials:"include"}).then(r=>r.ok?r.json():[]);
    setProjects(items); setToast(`已导入项目：${result.name}`);
  };
  const exportProjectPackage = (project:ProjectSummary) => {
    window.location.assign(`/api/projects/${project.id}/package`);
    setToast(`正在生成“${project.name}”便携项目包`);
  };
  const purgeExpiredTrash = async () => {
    if (!window.confirm("永久清理已超过 30 天保留期的项目？未到期项目不会受影响，此操作无法撤销。")) return;
    const response = await fetch("/api/trash/purge", {method:"DELETE",credentials:"include"});
    const result = await response.json().catch(()=>({}));
    if (!response.ok) { setToast(result.detail || "到期项目清理失败"); return; }
    await showTrash(); setToast(result.purged ? `已永久清理 ${result.purged} 个到期项目` : "没有超过 30 天保留期的项目");
  };
  const toggleProjectQualityMode = async (project:ProjectSummary) => {
    const nextMode = project.quality_mode === "balanced" ? "quality" : "balanced";
    const response = await fetch(`/api/projects/${project.id}/quality-mode`, {
      method:"PATCH",
      credentials:"include",
      headers:{"content-type":"application/json"},
      body:JSON.stringify({quality_mode:nextMode}),
    });
    if (!response.ok) { setToast("质量模式切换失败，请稍后重试"); return; }
    const updated:ProjectSummary = await response.json();
    setProjects(items=>items.map(item=>item.id===updated.id?{...item,...updated}:item));
    if (currentProject?.id === updated.id) setCurrentProject({...currentProject,...updated});
    setToast(nextMode === "quality" ? "已切换为质量优先，并创建检查点" : "已切换为平衡模式，并创建检查点");
  };
  const toggleAnnotations = () => {
    setAnnotationsEnabled(enabled => {
      const next = !enabled;
      window.localStorage.setItem("nature-workbench-annotations", next ? "on" : "off");
      if (account) fetch("/api/account/preferences", {method:"PATCH",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({annotations_enabled:next})}).then(response=>response.ok?response.json():null).then(updated=>{if(updated)setAccount(updated)}).catch(()=>undefined);
      setToast(next ? "英文注解已开启" : "英文注解已关闭；科研选项保持不变");
      return next;
    });
  };
  const loginAccount = async (event:React.FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setLoginBusy(true); setLoginError("");
    try {
      const session = await fetch("/api/session", {credentials:"include"});
      if (!session.ok) throw new Error("无法建立本机会话，请确认工作台服务正在运行");
      const response = await fetch("/api/account/login", {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({username:loginUsername,password:loginPassword})});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) { setLoginError(result.detail || "登录失败"); return; }
      const signedIn:LocalAccount = result.account; setAccount(signedIn);
      if (typeof signedIn.preferences.annotations_enabled === "boolean") setAnnotationsEnabled(signedIn.preferences.annotations_enabled);
      if (signedIn.preferences.last_screen && signedIn.preferences.last_screen !== "workspace") setScreen(signedIn.preferences.last_screen);
      await refreshProtectedData(); setLoginPassword(""); setToast("已进入星钥科研智能工作台");
    } catch (error) {
      setLoginError(error instanceof Error ? error.message : "无法连接本机工作台");
    } finally { setLoginBusy(false); }
  };
  const logoutAccount = async () => {
    if(assistantDirty && !window.confirm("工作稿有未保存编辑，确认退出登录并放弃编辑？"))return;
    if(paperDirty && !window.confirm("Paper 有未保存草稿，确认退出登录并放弃草稿？"))return;
    setAssistantDirty(false);
    await fetch("/api/account/logout", {method:"POST",credentials:"include"});
    setPaperMounted(false);setPaperDirty(false);setAssistantOpen(false);
    setAccount(null); setProjects([]); setCurrentProject(null); setScreen("home"); setLoginPassword("");
  };
  const changeAccountPassword = async () => {
    if (newPassword.length < 8) { setToast("新密码至少需要 8 个字符"); return; }
    setPasswordBusy(true);
    try {
      const response = await fetch("/api/account/password", {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({current_password:currentPassword,new_password:newPassword})});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) { setToast(result.detail || "密码修改失败"); return; }
      setAccount(result); setCurrentPassword(""); setNewPassword(""); setToast("账户密码已更新，明文未写入数据库");
    } finally { setPasswordBusy(false); }
  };
  const saveModelSettings = async () => {
    if (!modelProfileName.trim() || !modelBaseUrl.trim() || !modelName.trim()) { setToast("请填写配置名称、Base URL 和模型 ID"); return; }
    if (modelKind === "online" && !apiKey.trim()) { setToast("在线模型首次保存必须填写 API Key"); return; }
    const response = await fetch("/api/settings/models", {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({name:modelProfileName,kind:modelKind,base_url:modelBaseUrl,model:modelName,api_key:apiKey || null})});
    const result = await response.json().catch(()=>({}));
    if (!response.ok) { setToast(result.detail || "模型配置保存失败"); return; }
    setModelProfiles(items=>[result,...items]); setModelProfileName(""); setModelBaseUrl(""); setModelName(""); setApiKey(""); setToast(result.enabled ? "首个模型已保存并启用；密钥已进入 Windows 凭据管理器" : "模型已保存；当前启用模型未改变");
  };
  const toggleModelProfile = async (profile:ModelProfile) => {
    const action = profile.enabled ? "deactivate" : "activate";
    const response = await fetch(`/api/settings/models/${profile.id}/${action}`, {method:"POST",credentials:"include"});
    if (!response.ok) { const error=await response.json().catch(()=>({}));setToast(error.detail || "模型启停失败");return; }
    const profiles = await fetch("/api/settings/models", {credentials:"include"}).then(r=>r.ok?r.json():[]); setModelProfiles(profiles); setToast(profile.enabled ? "在线 AI 已停用，助手将使用本地规则" : `已启用 ${profile.name}；其他模型已自动停用`);
  };
  const deleteModelProfile = async (profile:ModelProfile) => {
    if (!window.confirm(`删除模型配置“${profile.name}”？对应凭据也会从 Windows 凭据管理器移除。`)) return;
    const response = await fetch(`/api/settings/models/${profile.id}`, {method:"DELETE",credentials:"include"});
    if (response.ok) { setModelProfiles(items=>items.filter(item=>item.id!==profile.id));setToast("模型配置及对应凭据已删除"); }
  };
  const migrateProjectsRoot = async () => {
    if (!window.confirm(`确认把现有项目迁移到：\n${projectsRoot}\n\n迁移前会检查重名冲突。`)) return;
    const response = await fetch("/api/settings/projects-root", {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({path:projectsRoot,migrate_existing:true,confirmed:true})});
    const result = await response.json().catch(()=>({})); setToast(response.ok ? `项目目录已迁移，共 ${result.migrated} 个项目` : "目录迁移失败：请检查路径、权限或重名冲突");
  };
  const browseProjectsRoot = async () => {
    setFolderPickerBusy(true);
    try {
      const response = await fetch("/api/settings/projects-root/browse", {method:"POST",credentials:"include"});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) { setToast(result.detail || "无法打开 Windows 文件夹选择器"); return; }
      if (result.selected) { setProjectsRoot(result.path);setToast("已选择文件夹；点击“应用并迁移”后才会变更项目位置"); }
      else setToast("已取消文件夹选择，当前保存位置未改变");
    } finally { setFolderPickerBusy(false); }
  };
  const checkForUpdates = async () => {
    if (!updateStatus.configured) { setToast("未配置发布源，未发起网络请求"); return; }
    setUpdateChecking(true); setUpdateResult(null);
    try {
      const response = await fetch("/api/settings/update/check", {method:"POST",credentials:"include"});
      const result = await response.json().catch(()=>({}));
      if (!response.ok) { setUpdateMessage(result.detail || "更新检查失败"); return; }
      setUpdateResult(result); setUpdateMessage(result.message);
    } finally { setUpdateChecking(false); }
  };
  const reorderPanel = (sourceId:string, targetId:string) => {
    setPanelOrder(items => {
      const source = items.indexOf(sourceId), target = items.indexOf(targetId);
      if (source < 0 || target < 0 || source === target) return items;
      const next = [...items]; next.splice(source,1); next.splice(target,0,sourceId); return next;
    });
  };
  const saveComposition = async () => {
    if (!currentProject) { setToast("示例组图不会写入项目"); return; }
    const limit={"1x2":2,"2x2":4,"hero_side":4,"vertical":6}[compositionTemplate];
    if (!compositionItems.length) { setToast("请先接受图片素材或生成数据图"); return; }
    if (compositionItems.length>limit) { setToast(`当前模板最多容纳 ${limit} 个面板`); return; }
    setCompositionSaving(true);
    try {
      const panels=compositionItems.map((item,index)=>({kind:item.kind,source_id:item.id,label:String.fromCharCode(97+index),crop_zoom:panelZoom[item.key] || 1}));
      const response=await fetch(`/api/projects/${currentProject.id}/composition-jobs`,{method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({anchor_id:selectedAnchor?.id || null,caption:compositionCaption.trim(),template:compositionTemplate,asset_ids:panels.filter(panel=>panel.kind==="asset").map(panel=>panel.source_id),chart_ids:panels.filter(panel=>panel.kind==="chart").map(panel=>panel.source_id),panels,labels:panels.map(panel=>panel.label),gap_px:compositionGap,aspect_ratio:compositionAspect,width_mm:compositionWidth,dpi:compositionDpi})});
      const queued=await response.json().catch(()=>({}));
      if (!response.ok) { setToast(typeof queued.detail==="string"?queued.detail:"组图任务创建失败，请检查面板来源"); return; }
      setToast("组图已进入后台队列，可在任务中心暂停或恢复");
      const result=await waitForPersistentJob(queued.job_id) as {preview_url:string;download_urls:Record<string,string>;warnings:string[]};
      setCompositionPreview(`/api${result.preview_url}`); setCompositionDownloads(Object.fromEntries(Object.entries(result.download_urls || {}).map(([format,url])=>[format,`/api${url}`]))); setCompositionWarnings(result.warnings || []); setToast(result.warnings?.length?`组图已生成，仍有 ${result.warnings.length} 项导出警告`:"投稿级组图已生成并创建检查点");
    } finally { setCompositionSaving(false); }
  };
  const openAssistant = () => {setAssistantSelection(selectedAnchor?.text||"");setAssistantOpen(true)};
  const startTopic = async () => {
    if(!topic.trim()||!allowProjectChange())return;setAssistantDirty(false);setTopicBusy(true);
    try{const project=await academicRequest("/api/projects",{name:topic.trim(),purpose:"research"});await loadProject(project.id,true);setAssistantSelection("");setAssistantOpen(true);}
    catch(error){setToast(error instanceof Error?error.message:"项目创建失败");}
    finally{setTopicBusy(false)}
  };
  const verifyReference=async(id:string)=>{
    setCitationChecks(items=>({...items,[id]:"正在核对引用…"}));
    try{const result=await academicRequest("/api/assets/"+id+"/verify-reference",{});setCitationChecks(items=>({...items,[id]:result.message||result.status}));}
    catch(error){setCitationChecks(items=>({...items,[id]:error instanceof Error?error.message:"核对失败"}));}
  };
  const saveSourceEdit = async () => {
    if (sourceIssues.length) {setToast(sourceIssues[0]);return;}
    if (!editingCandidate || isDemoCandidateId(editingCandidate.id)) { setEditingCandidate(null); setToast("示例素材不会写入项目"); return; }
    const response = await fetch(`/api/assets/${editingCandidate.id}`, {method:"PATCH",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({title:sourceDraft.title,source_url:sourceDraft.sourceUrl,author:sourceDraft.author,license_name:sourceDraft.license,license_url:sourceDraft.licenseUrl || null,reason:sourceDraft.reason,reuse_status:sourceDraft.reuseStatus,license_verified:sourceDraft.verified})});
    if (response.ok) {
      const updated:CandidateApiRecord = await response.json();
      setCandidates(items=>items.map(item=>item.id===editingCandidate.id?candidateFromApi({...updated,citation_action:item.citationSelected?"accept":null,decision_action:item.status==="accepted"?"accept":item.status==="rejected"?"reject":null}):item));
      setEditingCandidate(null); setToast(sourceDraft.verified ? "图片许可核验已保存；文献可直接使用引用入口" : "来源已修改并重置为需核验");
    }
    else { const failure=await response.json().catch(()=>({})); setToast(typeof failure.detail==="string"?failure.detail:"来源未保存，请检查网址与修改理由"); }
  };

  const openFullDocumentEditor = () => {
    if (!currentProject || !anchors.length) { setToast("示例内容只读；上传真实文档后可编辑全文"); return; }
    const drafts = anchors.map(anchor=>({...anchor}));
    setDocumentDrafts(drafts); setDocumentFullText(drafts.map(draft=>draft.text).join("\n\n")); setDocumentEditReason(""); setDocumentFind(""); setDocumentReplace(""); setDocumentFindCursor(-1); setDocumentEditorOpen(true);
  };
  const focusDocumentMatch = (direction:1|-1) => {
    if (!documentFind) { setToast("请先输入要查找的文字"); return; }
    if (!documentFindMatches.length) { setDocumentFindCursor(-1); setToast("全文中未找到该文字"); return; }
    const nextIndex = direction === 1
      ? (documentFindCursor + 1 + documentFindMatches.length) % documentFindMatches.length
      : (documentFindCursor - 1 + documentFindMatches.length) % documentFindMatches.length;
    const match = documentFindMatches[nextIndex]; const textarea = fullDocumentTextareaRef.current;
    setDocumentFindCursor(nextIndex);
    if (textarea) { textarea.focus(); textarea.setSelectionRange(match.start, match.end); textarea.scrollIntoView({behavior:"smooth",block:"center"}); }
  };
  const replaceAcrossDocument = () => {
    if (!documentFind) { setToast("请先输入要查找的文字"); return; }
    const replacements = documentFullText.split(documentFind).length-1;
    if (replacements) setDocumentFullText(documentFullText.split(documentFind).join(documentReplace));
    setDocumentFindCursor(-1); setToast(replacements ? `已在全文替换 ${replacements} 处；尚未保存` : "全文中未找到该文字");
  };
  const saveFullDocument = async () => {
    if (!currentProject || !documentStructureValid || !changedDocumentCount || documentEditReason.trim().length<3) return;
    setDocumentSaving(true);
    try {
      const response = await fetch(`/api/projects/${currentProject.id}/document-content`, {method:"PATCH",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({document_id:currentProject.manuscript_document_id,anchors:documentDrafts.map((item,index)=>({id:item.id,text:documentParagraphTexts[index]})),reason:documentEditReason})});
      if (!response.ok) { setToast(response.status===409 ? "全文没有可保存的变化，或文档版本已更新" : "全文修订保存失败"); return; }
      const result = await response.json(); setAnchors(result.anchors); setDocumentEditorOpen(false); setToast(`全文修订已保存，共更新 ${result.changed_count} 个段落；原文件未覆盖`);
    } finally { setDocumentSaving(false); }
  };
  const undoLatestDocumentEdit = async () => {
    if (!currentProject) { setToast("请先打开真实项目"); return; }
    const revisions:DocumentRevision[] = await fetch(`/api/projects/${currentProject.id}/document-revisions`, {credentials:"include"}).then(r=>r.ok?r.json():[]);
    if (!revisions.length) { setToast("当前文档没有可撤销的全文修订"); return; }
    const response = await fetch(`/api/document-revisions/${revisions[0].id}/restore`, {method:"POST",credentials:"include"});
    if (!response.ok) { setToast("全文撤销失败；文档可能已发生新的结构变化"); return; }
    const result = await response.json(); setAnchors(result.anchors); setToast(`已撤销上一项全文修订，恢复 ${result.changed_count} 个段落`);
  };
  const confirmSelectedOcr = async () => {
    if (!selectedAnchor || selectedAnchor.ocr_confidence === null) return;
    setOcrConfirming(true);
    try {
      const response = await fetch(`/api/anchors/${selectedAnchor.id}/ocr-confirmation`, {method:"POST",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({reason:"用户在全文工作台人工核对 OCR 文本"})});
      if (!response.ok) { setToast("OCR 确认未保存，请重试"); return; }
      const updated:AnchorRecord = await response.json();
      setAnchors(items=>items.map(item=>item.id===updated.id?updated:item));
      setToast("OCR 文本已人工确认并写入审计记录");
    } finally { setOcrConfirming(false); }
  };
  const controlJob = async (job:JobRecord, action:"pause"|"resume"|"cancel"|"retry") => {
    const response = await fetch(`/api/jobs/${job.id}`, {method:"PATCH",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({action})});
    if (!response.ok) { setToast("任务状态已经变化，请刷新后重试"); return; }
    const updated:JobRecord = await response.json(); setJobs(items=>items.map(item=>item.id===updated.id?{...item,...updated}:item));
    setToast({pause:"任务将在当前阶段结束后暂停",resume:"任务已重新进入队列",cancel:"任务将在当前阶段结束后取消",retry:"任务已从头进入重试队列"}[action]);
  };
  useEffect(() => {
    if (!jobsOpen) return;
    let active = true;
    let fallbackTimer:number|undefined;
    const refresh = async () => {
      setJobsLoading(true);
      try {
        const response = await fetch("/api/jobs?limit=100", {credentials:"include"});
        if (active && response.ok) setJobs(await response.json());
      } finally { if (active) setJobsLoading(false); }
    };
    refresh();
    const events = new EventSource("/api/jobs/events/stream?limit=100", {withCredentials:true});
    events.addEventListener("jobs", event => {
      if (!active) return;
      try { setJobs(JSON.parse((event as MessageEvent).data)); setJobsLoading(false); } catch { /* polling fallback remains available */ }
    });
    events.onerror = () => {
      events.close();
      if (active && fallbackTimer === undefined) fallbackTimer = window.setInterval(refresh, 1400);
    };
    return () => { active = false; events.close(); if (fallbackTimer !== undefined) window.clearInterval(fallbackTimer); };
  }, [jobsOpen]);
  useEffect(() => {
    if (!account?.id) return;
    const timer = window.setTimeout(() => {
      fetch("/api/account/preferences", {method:"PATCH",credentials:"include",headers:{"content-type":"application/json"},body:JSON.stringify({last_screen:screen})}).then(response=>response.ok?response.json():null).then(updated=>{if(updated)setAccount(updated)}).catch(()=>undefined);
    }, 450);
    return () => window.clearTimeout(timer);
  }, [screen,account?.id]);

  if (!accountReady) return <main className="account-gate"><div className="account-card account-loading" role="status"><div className="brand-mark"><BookOpenText size={22}/></div><h1>正在连接本机账户</h1><p>正在建立仅限 127.0.0.1 的安全会话…</p></div></main>;
  if (!account) return <main className="account-gate">
    <section className="account-card" aria-labelledby="account-login-title">
      <div className="account-brand"><div className="brand-mark"><BookOpenText size={22}/></div><div><strong>星钥科研智能工作台</strong><span>本机科研账户</span></div></div>
      <div className="eyebrow">Local account</div>
      <h1 id="account-login-title">登录星钥科研智能工作台</h1>
      <p>账户用于保存个人偏好和保护本机项目入口。开发账户首次登录后应立即修改初始密码。</p>
      <form className="account-form" onSubmit={loginAccount}>
        <div className="field"><label htmlFor="login-username">用户名</label><input id="login-username" className="input" autoComplete="username" value={loginUsername} onChange={event=>setLoginUsername(event.target.value)}/></div>
        <div className="field"><label htmlFor="login-password">密码</label><input id="login-password" className="input" type="password" autoComplete="current-password" value={loginPassword} onChange={event=>setLoginPassword(event.target.value)}/></div>
        {loginError && <div className="account-error" role="alert"><CircleAlert size={15}/><span>{loginError}</span></div>}
        <button className="button primary account-submit" disabled={loginBusy || !loginUsername || !loginPassword} type="submit"><ShieldCheck size={16}/>{loginBusy?'正在验证…':'登录本机账户'}</button>
      </form>
      <div className="account-security-note"><ShieldCheck size={15}/><span>密码使用 PBKDF2 带盐哈希保存；连续失败会短时锁定。正文、数据和模型密钥不写入账户表。</span></div>
    </section>
  </main>;

  return <AnnotationContext.Provider value={annotationsEnabled}><Tooltip.Provider delayDuration={350}>
    <div className={`app-shell ${screen==="paper"?"paper-mode":""}`}>
      <aside className="app-nav" aria-label="主导航">
        <div className="brand"><div className="brand-mark"><BookOpenText size={20}/></div><div className="brand-copy"><strong>星钥科研智能工作台</strong><span>学术研究助手</span></div></div>
        <nav className="nav-list">
          <button aria-label="开始" title="开始" className={`nav-item ${screen === 'home' ? 'active' : ''}`} onClick={() => setScreen("home")}><LayoutDashboard size={17}/><span>开始</span></button>
          <button aria-label="工作台" title="工作台" className={`nav-item ${screen === 'workspace' ? 'active' : ''}`} onClick={()=>currentProject ? setScreen("workspace") : openDemo()}><FileSearch size={17}/><span>工作台</span></button>
          
          <button aria-label="实验记录 Paper" title="实验记录 Paper" className={`nav-item ${screen === 'paper' ? 'active' : ''}`} onClick={()=>setScreen("paper")}><BookOpenText size={17}/><span>实验记录 Paper</span></button>
          <button aria-label="项目库" title="项目库" className={`nav-item ${screen === 'library' ? 'active' : ''}`} onClick={showLibrary}><Library size={17}/><span>项目库</span></button>
          <button aria-label="回收站" title="回收站" className={`nav-item ${screen === 'trash' ? 'active' : ''}`} onClick={showTrash}><Archive size={17}/><span>回收站</span></button>
        </nav>
        <div className="nav-spacer"/>
        <nav className="nav-list"><button aria-label="设置" title="设置" className={`nav-item ${screen === 'settings' ? 'active' : ''}`} onClick={()=>setScreen('settings')}><Settings size={17}/><span>设置</span></button></nav>
        <div className="nav-status"><div className="status-row"><span className="status-copy">{account.username}</span><span className="status-dot" aria-label={apiReady ? "已连接" : "前端演示模式"}/></div><div className="status-row"><span className="status-copy">数据仅存本机</span><ShieldCheck size={14}/></div></div>
      </aside>
      <section className="mobile-status-panel" aria-labelledby="mobile-status-title">
        <div className="brand-mark"><Clock3 size={21}/></div>
        <div className="eyebrow">Task status only</div>
        <h1 id="mobile-status-title">手机端仅查看任务状态</h1>
        <p>为避免在窄屏误改正文、来源或统计参数，项目管理与编辑功能已隐藏。请使用桌面窗口完成科研操作。</p>
        <button className="button primary" onClick={()=>setJobsOpen(true)}><Clock3 size={16}/>打开任务中心</button>
        <div className="mobile-account-status"><ShieldCheck size={14}/><span>{account.username} · 数据仅存本机</span></div>
      </section>
      <main className="main-area">
        <input hidden tabIndex={-1} ref={fileInput} className="sr-only" type="file" accept=".docx,.pdf,.md,.tex,.csv,.xlsx" multiple={screen==="workspace"} onChange={(event:ChangeEvent<HTMLInputElement>)=>{const files=event.currentTarget.files;void onFiles(files);event.currentTarget.value="";}}/>
        <header className="topbar">
          <div className="topbar-title">{screen === "home" ? <><span>个人工作台</span><ChevronRight size={13}/><strong>开始新项目</strong></> : screen === "workspace" ? <><span>项目</span><ChevronRight size={13}/><strong>{currentProject?.name || "城市热风险与绿色基础设施"}</strong></> : <><span>个人工作台</span><ChevronRight size={13}/><strong>{screen === "library" ? "项目库" : screen === "trash" ? "30 天回收站" : screen === "paper" ? "实验记录" : "设置"}</strong></>}</div>
          <div className="top-actions"><div id="paper-top-actions"/>
            {screen === "workspace" && <><TooltipButton label="任务中心" onClick={()=>setJobsOpen(true)}><Clock3 size={17}/></TooltipButton><TooltipButton label={dataOnlyProject?"纯数据项目没有正文修订":"撤销最近一次全文修订"} disabled={!anchors.length} onClick={undoLatestDocumentEdit}><History size={17}/></TooltipButton><TooltipButton label={inspectorOpen ? "收起建议面板并扩展正文" : "展开建议面板"} pressed={!inspectorOpen} onClick={() => setInspectorOpen(v => !v)}><PanelRight size={17}/></TooltipButton><button className="button secondary" disabled={!anchors.length} onClick={openFullDocumentEditor}><Pencil size={15}/>编辑全文</button><button className="button primary" onClick={() => setExportOpen(true)}><Download size={15}/>导出</button></>}
            {screen === "home" && <button className="button ghost" onClick={()=>setJobsOpen(true)}>任务中心</button>}
          </div>
        </header>
        {(paperMounted || screen === "paper") && <div className="paper-host" style={{display:screen === "paper" ? "block" : "none"}}><PaperPage active={screen==="paper"} onDirtyChange={setPaperDirty}/></div>}
        {screen === "paper" ? null : screen === "home" ? <section className="home">
          <div className="hero-grid">
            <div className="upload-card"><div className="academic-welcome"><div className="eyebrow">学术工作台</div><h1>开始研究</h1></div><div className={`dropzone ${dragging ? 'dragging' : ''}`} onDragEnter={e=>{e.preventDefault();setDragging(true)}} onDragOver={e=>e.preventDefault()} onDragLeave={()=>setDragging(false)} onDrop={(e:DragEvent)=>{e.preventDefault();setDragging(false);onFiles(e.dataTransfer.files)}}>
              <div><div className="upload-icon"><Upload size={24}/></div><h2>从文档开始</h2><p>拖入文档或数据文件</p><button className="button primary" onClick={() => fileInput.current?.click()}><FolderOpen size={15}/>选择文件</button><div className="file-note"><TranslatedTerm term="DOCX · PDF · Markdown · LaTeX · CSV · XLSX" meaning="Word 文档、便携文档、轻量标记、论文排版源码、逗号分隔数据与 Excel 工作簿"/></div></div>
            </div></div></div>
          <section className="academic-topic"><div><h2>还没有文稿？从选题开始</h2><p>输入研究方向，开始讨论。</p></div><label className="sr-only" htmlFor="new-topic">研究方向或项目名称</label><input id="new-topic" maxLength={160} className="input" value={topic} onChange={e=>setTopic(e.target.value)} placeholder="例如：城市绿地与热风险"/><button className="button secondary" disabled={topicBusy||!topic.trim()} onClick={startTopic}>{topicBusy?"创建中…":"开始讨论"}</button></section>
          <div className="section-heading"><div><h2>最近项目</h2></div><button className="button ghost" onClick={showLibrary}>查看全部 <ChevronRight size={14}/></button></div>
          <div className="project-grid">
            {(projects.length ? projects.slice(0,3) : [
              {id:'demo',name:'城市热风险与绿色基础设施',status:'正在审核 4 个锚点',updated:'今天 18:42',progress:68},
              {id:'demo2',name:'复合材料疲劳损伤技术报告',status:'已完成数据制图',updated:'昨天 21:10',progress:86},
              {id:'demo3',name:'空域协同优化方法',status:'等待隐私确认',updated:'7 月 16 日',progress:34},
            ]).map(p=><button className="project-card" key={p.id} onClick={()=>p.id.startsWith("demo") ? openDemo() : loadProject(p.id).catch(()=>setToast("项目读取失败"))}><div className="project-meta"><span>{p.updated}</span><MoreHorizontal size={16}/></div><h3>{p.name}</h3><p>{p.status}</p><div className="progress"><span style={{width:`${p.progress}%`}}/></div></button>)}
          </div>
          <footer className="academic-runtime" aria-label="运行状态">服务：{apiReady?"正常":"离线"} · 图片识别：{runtimeHealth.ocr?.required_ready?"就绪":"需检查"} · {enabledModel?enabledModel.name:"本地模板模式"} · 版本 {runtimeHealth.version||"读取中"} <button className="button ghost compact" onClick={()=>setScreen("settings")}>运行与模型设置</button></footer>
        </section> : screen === "workspace" ? <section className={`workspace ${inspectorOpen ? '' : 'inspector-collapsed'} ${outlineOpen?'':'outline-collapsed'}`}>
          {!outlineOpen&&<aside className="outline-rail"><button className="icon-btn" title="展开目录" aria-label="展开目录" aria-expanded={false} onClick={()=>setOutlineOpen(true)}><PanelLeftOpen size={18}/></button></aside>}{outlineOpen && <aside id="document-outline" className="outline-panel" aria-label="文档章节"><div className="outline-heading"><span className="panel-label">文档结构</span><button className="icon-btn" aria-label="收起目录" aria-expanded={true} aria-controls="document-outline" onClick={()=>setOutlineOpen(false)}><PanelLeftClose size={18}/></button></div>{sections.length ? sections.map(([section,count])=><button className={`outline-item ${selectedAnchor?.section===section?'active':''}`} key={section} onClick={()=>{const target=anchors.findIndex(anchor=>anchor.section===section);if(target>=0)selectDocumentParagraph(target)}}><ChevronRight size={13}/>{section}<span className="count">{count}</span></button>) : currentProject ? <div className="outline-empty"><FileChartColumn size={18}/><strong>{dataOnlyProject?"纯数据项目":mode==="research"?"选题与写作":"文档状态"}</strong><span>暂无正文章节</span></div> : <><button className="outline-item active"><ChevronRight size={13}/>摘要<span className="count">1</span></button><button className="outline-item"><ChevronRight size={13}/>1. 研究背景<span className="count">2</span></button><button className="outline-item"><ChevronRight size={13}/>2. 数据与方法<span className="count">3</span></button><button className="outline-item"><ChevronRight size={13}/>3. 结果<span className="count">4</span></button><button className="outline-item"><ChevronRight size={13}/>4. 讨论<span className="count">2</span></button><button className="outline-item"><ChevronRight size={13}/>5. 结论<span className="count">1</span></button></>}</aside>}
          <div className="document-stage"><article className="document-page">{currentProject?.manuscript_required&&<p className="notice">请在“材料”中选择正文；补充材料仍可独立阅读。</p>}{currentProject&&<details className="material-menu"><summary className="button secondary compact">材料</summary><div className="material-menu-content"><button className="button secondary" disabled={uploadBusy} onClick={openMaterialUpload}>{uploadBusy?"处理中…":"添加研究材料"}</button><button className="button ghost" disabled={!currentProject.documents?.length} onClick={()=>setReaderOpen(true)}>阅读材料</button><button className="button ghost" disabled={!anchors.length} onClick={()=>void openPrivacyPreview()}>检查当前段落敏感信息</button>{currentProject.documents?.map(doc=><div key={doc.id}><a href={`/api/projects/${currentProject.id}/documents/${doc.id}/original`} target="_blank" rel="noreferrer">{doc.filename}</a><button className="button ghost compact" disabled={currentProject.manuscript_document_id===doc.id||doc.parser==="pending"} onClick={()=>void selectManuscript(doc.id)}>{currentProject.manuscript_document_id===doc.id?"当前正文":"设为正文"}</button></div>)}{materialDatasets.map(profile=><button key={profile.id} className="button ghost" onClick={()=>selectMaterialDataset(profile)}>使用数据：{profile.filename}</button>)}</div></details>}<div className="doc-kicker"><TranslatedTerm term={dataOnlyProject ? "Data project" : currentProject ? (anchors.length ? `已解析文档${selectedAnchor?.page ? ` · Page ${selectedAnchor.page}` : ""}` : mode==="research" ? "选题与写作" : "文档状态") : "Research article · Page 2 of 14"} meaning={dataOnlyProject ? "纯数据项目：当前没有上传论文或技术文档" : currentProject ? (anchors.length ? "已解析文档及当前原始页码" : mode==="research" ? "选题项目，可先整理文献与工作稿" : "文档上传与解析状态") : "研究论文示例，第 2 页，共 14 页"}/></div><h1>{currentProject?.name || "城市热风险的空间异质性与绿色基础设施响应"}</h1><div className="doc-authors">{dataOnlyProject ? "纯数据项目 · 可直接制图，未上传文档正文" : currentProject ? (anchors.length ? `${anchors.length} 段正文 · 可编辑全文` : mode==="research" ? "选题项目 · 可先整理文献与工作稿" : "查看上传与解析状态") : "示例作者 · 城市环境与韧性研究组"}</div>{selectedAnchor?.ocr_confidence !== null && selectedAnchor?.ocr_confidence !== undefined && !selectedAnchor.ocr_confirmed && <div className={`ocr-review ${selectedAnchor.ocr_confidence < .8 ? 'low' : ''}`} role="status"><CircleAlert size={16}/><div><strong><TranslatedTerm term="OCR" meaning="光学字符识别：把扫描页图像转换为可编辑文本"/> 待人工核对 · {Math.round(selectedAnchor.ocr_confidence*100)}%</strong><span>{selectedAnchor.ocr_rotation ? `页面已自动旋转 ${selectedAnchor.ocr_rotation}°。` : "未检测到页面旋转。"} 如需修正，请先使用“编辑全文”，再确认当前段落。</span></div><button className="button secondary compact" disabled={ocrConfirming} onClick={confirmSelectedOcr}>{ocrConfirming?'保存中…':'确认当前文本'}</button></div>}{currentProject && !anchors.length && !dataOnlyProject && <DocumentStatus mode={mode} projectId={currentProject.id} documents={currentProject.documents||[]} busy={uploadBusy} message={mode==="document_failed"?currentProject.jobs?.find(job=>job.kind==="document_analysis")?.message:undefined} onUpload={openMaterialUpload} onRetry={retryDocument}/>} {dataOnlyProject&&<div className="document-empty-state"><FileChartColumn size={30}/><div><strong>当前项目没有文档正文</strong><span>在右侧选择字段生成图表。</span></div></div>}{displayParagraphs.map((p,i)=>{const section=anchors[i]?.section || "摘要";const showSection=i===0 || (!!anchors.length && anchors[i-1]?.section!==section);return <Fragment key={`${i}-${anchors[i]?.id || p.slice(0,18)}`}>{showSection&&<h2>{section}</h2>}<p className={`doc-paragraph ${selectedParagraph===i?'selected':''} ${anchors[i]?.ocr_confidence!==null && anchors[i]?.ocr_confidence!==undefined && anchors[i].ocr_confidence<.8 && !anchors[i].ocr_confirmed?'ocr-low':''}`} onClick={()=>selectDocumentParagraph(i)}>{p}<span className="paragraph-citations" aria-live="polite">{(citationProjection?.paragraph_citations[anchors[i]?.id]||[]).map(number=><a className="citation-marker" key={number} href={`#reference-${number}`} onClick={event=>event.stopPropagation()} aria-label={`参考文献 ${number}`}>[{number}]</a>)}</span><span className="paragraph-actions"><button type="button" className="anchor-button" aria-label={`查看第 ${i+1} 段文献与图表`} onClick={event=>{event.stopPropagation();selectDocumentParagraph(i,true)}}><Plus size={13}/></button></span></p></Fragment>})}{chartPreview ? <div className="figure-placeholder"><Image unoptimized width={800} height={450} className="generated-chart" src={chartPreview} alt="用户数据生成的科研图草稿"/><div className="figure-caption"><strong>图表草稿｜</strong>由上传数据和当前 图表设置生成，统计信息将在导出前核对。</div></div> : currentProject && !anchors.length && !dataset ? null : currentProject ? <div className="figure-placeholder figure-empty-state"><BarChart3 size={28}/><div><strong>尚未生成项目数据图</strong><span>上传 CSV/XLSX 后生成图表。</span></div></div> : <div className="figure-placeholder"><div className="chart-preview">{[44,78,58,104,86,122].map((h,i)=><span key={i} className={`chart-bar ${i%2?'alt':''}`} style={{height:h}}/>)}</div><div className="figure-caption"><strong>图 1｜</strong>不同绿地覆盖情景下的街区尺度降温响应。误差线与统计检验信息将在导出前完成核对。</div></div>}{citationError&&<p role="alert">{citationError}</p>}{!!citationProjection?.bibliography.length&&<section className="document-references"><h2>参考文献</h2><ol>{citationProjection.bibliography.map(ref=><li id={`reference-${ref.number}`} key={ref.reference_key} value={ref.number}><span>[{ref.number}] </span>{ref.author?ref.author+". ":""}{ref.title}{ref.journal?". "+ref.journal:""}{ref.year?" ("+ref.year+")":""}{ref.source_url&&<> · <a href={ref.source_url} target="_blank" rel="noreferrer">来源</a></>}</li>)}</ol></section>}</article></div>
          {inspectorOpen && <aside className="inspector" aria-label="文献与图表"><Tabs.Root value={activeTab} onValueChange={setActiveTab}><div className="inspector-head"><div className="inspector-title"><strong>{dataOnlyProject?"项目级制图":currentProject&&!selectedAnchor?"项目文献与图表":`段落 ${selectedParagraph+1} · 文献与图表`}</strong>{dataOnlyProject&&<span className="badge safe"><FileChartColumn size={11}/>数据模式</span>}</div><Tabs.List className="tab-list" aria-label="建议类型"><Tabs.Trigger className="tab-trigger" value="assets">文献检索</Tabs.Trigger><Tabs.Trigger className="tab-trigger" value="chart">数据制图</Tabs.Trigger><Tabs.Trigger className="tab-trigger" value="compose">组图</Tabs.Trigger><Tabs.Trigger className="tab-trigger" value="caption">图注</Tabs.Trigger><Tabs.Trigger className="tab-trigger" value="source">来源</Tabs.Trigger></Tabs.List></div>
            <div className="inspector-content"><details className="context-card context-disclosure"><summary>{selectedAnchor || !currentProject ? "当前段落" : "图表保存位置"}</summary><p>{selectedAnchor || !currentProject ? displayParagraphs[selectedParagraph] : "图表保存在当前项目，可在有正文后指定插入位置。"}</p></details>
              <Tabs.Content value="assets"><div className="research-entry"><a className="button secondary" href={baiduScholarUrl(query)} target="_blank" rel="noreferrer"><ExternalLink size={14}/>百度学术</a><button className="button secondary" disabled={!currentProject} onClick={()=>setReferenceOpen(true)}><Plus size={14}/>添加引用链接</button></div><div className="search-box"><input className="input" value={query} onChange={e=>setQuery(e.target.value)} aria-label="文献检索关键词" placeholder="研究主题、关键词或论文标题"/><button className="icon-btn" aria-label="从当前段落提取关键词（本地）" onClick={suggestLocalQuery}><Sparkles size={16}/></button><button className="icon-btn" aria-label="搜索学术文献" disabled={!currentProject||!query.trim()} onClick={searchAssets}><Search size={16}/></button></div><p className="academic-help">搜索词将发送至文献服务。</p>{!candidates.length&&<p className="empty-note">输入关键词检索，或添加引用链接。</p>}<div className="literature-list">{candidates.filter(c=>c.status!=="rejected").map(c=><article className="literature-card" key={c.id}><span className="academic-source-label">{c.source}</span><h3>{c.title}</h3><p>{c.author||"作者信息待核对"}{c.year?" · "+c.year:""}{c.journal?" · "+c.journal:""}</p>{c.doi&&<p>DOI：{c.doi}</p>}{c.sourceUrl&&<a href={c.sourceUrl} target="_blank" rel="noreferrer">打开原文 <ExternalLink size={11}/></a>}<div className="candidate-actions"><button className="mini-button" disabled={isDemoCandidateId(c.id)} onClick={()=>verifyReference(c.id)}>核对引用</button><button className="mini-button accept" aria-busy={citationSaving===c.id} disabled={!!citationSaving||isDemoCandidateId(c.id)} onClick={()=>setCitationStatus(c.id,!(selectedAnchor?citationProjection?.bibliography.some(ref=>ref.reference_ids.includes(c.id)&&(citationProjection.paragraph_citations[selectedAnchor.id]||[]).includes(ref.number)):c.citationSelected))}><BookOpenText size={12}/>{citationSaving===c.id?"保存中…":(selectedAnchor?citationProjection?.bibliography.some(ref=>ref.reference_ids.includes(c.id)&&(citationProjection.paragraph_citations[selectedAnchor.id]||[]).includes(ref.number)):c.citationSelected)?"取消引用":selectedAnchor?"引用到当前段落":"收藏文献"}</button></div>{citationChecks[c.id]&&<p role="status">{citationChecks[c.id]}</p>}</article>)}</div></Tabs.Content>
              <Tabs.Content value="chart"><div className="chart-form"><p className="academic-help">数据图表需要 CSV 或 XLSX；原文图片在“论文阅读”中查看，流程与结构图可用“学术助手 → 图示”。</p><div className="notice"><CircleAlert size={16}/><span>选数据、选横轴和纵轴，即可先生成图表。投稿前再补充统计说明。</span></div><div className="field"><label>数据文件</label><TranslationTooltip term="CSV / XLSX" meaning="逗号分隔数据文件 / Excel 工作簿"><button className="button secondary" style={{width:"100%"}} onClick={()=>datasetInput.current?.click()}><Upload size={15}/>{dataset ? `${dataset.filename} · ${activeDatasetProfile?.rows ?? dataset.rows} 行` : "上传 CSV / XLSX"}</button></TranslationTooltip><input ref={datasetInput} className="sr-only" type="file" accept=".csv,.xlsx" onChange={(event)=>uploadDataset(event.target.files)}/></div>{(dataset?.sheets?.length || 0)>1 && <div className="field"><label htmlFor="dataset-sheet">Excel 工作表</label><TranslatedSelect id="dataset-sheet" value={sheetName} options={(dataset?.sheets || []).map(sheet=>({value:sheet.name,label:sheet.name,meaning:`工作表“${sheet.name}”，包含 ${sheet.rows} 行、${sheet.columns.length} 个字段`}))} onChange={selectDatasetSheet} ariaLabel="Excel 工作表"/></div>}<div className="field"><label htmlFor="chart-type">推荐图型</label><TranslatedSelect id="chart-type" value={chartType} options={(activeDatasetProfile?.recommended_charts || ["grouped_bar","scatter","heatmap","line","box"]).map(type=>({value:type,label:({grouped_bar:"柱状图",scatter:"散点图",heatmap:"相关性热图",line:"折线图",box:"箱线图"} as Record<string,string>)[type] || type,meaning:chartTypeMeanings[type] || "数据集中记录的图表类型标识"}))} onChange={setChartType} ariaLabel="推荐图型"/></div><div className="field"><label htmlFor="chart-title">图题</label><input id="chart-title" className="input" value={chartTitle} onChange={event=>setChartTitle(event.target.value)} placeholder={yField && xField ? `${yField} by ${xField}` : "Scientific figure"}/></div><div className="field"><label htmlFor="x-field">横轴：类别或时间</label><TranslatedSelect id="x-field" value={xField} options={datasetColumnOptions(activeDatasetProfile,"选择字段","请选择横轴、类别或分组字段")} onChange={setXField} ariaLabel="横轴：类别或时间"/></div><div className="field"><label htmlFor="y-field">纵轴：要比较的数值</label><TranslatedSelect id="y-field" value={yField} options={datasetColumnOptions(activeDatasetProfile,"选择字段","请选择需要分析或绘制的响应变量")} onChange={setYField} ariaLabel="纵轴：要比较的数值"/></div><div className="field"><label htmlFor="group-field">按哪一列区分颜色（可选）</label><TranslatedSelect id="group-field" value={groupField} options={datasetColumnOptions(activeDatasetProfile,"不分组","不使用颜色字段拆分数据系列")} onChange={setGroupField} ariaLabel="颜色 / 分组字段（可选）"/></div>
                <details className="chart-style-editor"><summary><span><Pencil size={14}/>图表外观（可选）</span><em>{chartStyle.widthMm} × {chartStyle.heightMm} mm · {chartStyle.fontFamily} · {chartStyle.gridMode==="none"?"无网格":"网格已启用"}</em><ChevronDown size={14}/></summary><div className="chart-style-body">
                  <fieldset><legend>画布与字体</legend><div className="chart-style-grid"><div className="field"><label htmlFor="chart-width">宽度（mm）</label><input id="chart-width" className="input" type="number" min="60" max="300" value={chartStyle.widthMm} onChange={event=>setChartStyle(style=>({...style,widthMm:Number(event.target.value)}))}/></div><div className="field"><label htmlFor="chart-height">高度（mm）</label><input id="chart-height" className="input" type="number" min="45" max="240" value={chartStyle.heightMm} onChange={event=>setChartStyle(style=>({...style,heightMm:Number(event.target.value)}))}/></div><div className="field"><label htmlFor="chart-font">字体</label><TranslatedSelect id="chart-font" value={chartStyle.fontFamily} options={[{value:"Arial",label:"Arial",meaning:"Nature 常用无衬线字体；中文会自动回退到微软雅黑"},{value:"Calibri",label:"Calibri",meaning:"Microsoft Office 常用字体，便于办公文档迁移"},{value:"Times New Roman",label:"Times New Roman",meaning:"衬线字体，适合特定期刊或传统排版"},{value:"Microsoft YaHei",label:"Microsoft YaHei",meaning:"微软雅黑：Windows 中文显示兼容性较好"}]} onChange={value=>setChartStyle(style=>({...style,fontFamily:value}))} ariaLabel="图表字体"/></div><div className="field"><label htmlFor="chart-font-size">正文字号（pt）</label><input id="chart-font-size" className="input" type="number" min="5" max="20" step="0.5" value={chartStyle.fontSizePt} onChange={event=>setChartStyle(style=>({...style,fontSizePt:Number(event.target.value)}))}/></div><div className="field"><label htmlFor="chart-title-size">标题字号（pt）</label><input id="chart-title-size" className="input" type="number" min="7" max="28" step="0.5" value={chartStyle.titleSizePt} onChange={event=>setChartStyle(style=>({...style,titleSizePt:Number(event.target.value)}))}/></div></div></fieldset>
                  <fieldset><legend>坐标轴与网格</legend><div className="chart-style-grid"><div className="field"><label htmlFor="chart-x-label">X 轴标题</label><input id="chart-x-label" className="input" value={chartStyle.xLabel} onChange={event=>setChartStyle(style=>({...style,xLabel:event.target.value}))} placeholder={xField || "X axis"}/></div><div className="field"><label htmlFor="chart-y-label">Y 轴标题</label><input id="chart-y-label" className="input" value={chartStyle.yLabel} onChange={event=>setChartStyle(style=>({...style,yLabel:event.target.value}))} placeholder={yField || "Y axis"}/></div><div className="field"><label htmlFor="chart-grid">网格线</label><TranslatedSelect id="chart-grid" value={chartStyle.gridMode} options={[{value:"none",label:"不显示",meaning:"Nature 风格默认值，以稀疏刻度代替网格"},{value:"y",label:"仅横向",meaning:"沿 Y 轴刻度显示水平辅助线"},{value:"x",label:"仅纵向",meaning:"沿 X 轴刻度显示垂直辅助线"},{value:"both",label:"横向＋纵向",meaning:"两个方向均显示浅色辅助线"}]} onChange={value=>setChartStyle(style=>({...style,gridMode:value}))} ariaLabel="图表网格线"/></div><div className="field"><label htmlFor="chart-ticks">主刻度数量</label><input id="chart-ticks" className="input" type="number" min="2" max="12" value={chartStyle.tickCount} onChange={event=>setChartStyle(style=>({...style,tickCount:Number(event.target.value)}))}/></div><div className="field"><label htmlFor="chart-x-scale">X 标尺</label><TranslatedSelect id="chart-x-scale" value={chartStyle.xScale} options={[{value:"linear",label:"线性",meaning:"线性标尺：等距离表示等数值差"},{value:"log",label:"对数",meaning:"对数标尺：适合跨多个数量级的正数"}]} onChange={value=>setChartStyle(style=>({...style,xScale:value}))} ariaLabel="X 轴标尺"/></div><div className="field"><label htmlFor="chart-y-scale">Y 标尺</label><TranslatedSelect id="chart-y-scale" value={chartStyle.yScale} options={[{value:"linear",label:"线性",meaning:"线性标尺：等距离表示等数值差"},{value:"log",label:"对数",meaning:"对数标尺：适合跨多个数量级的正数"}]} onChange={value=>setChartStyle(style=>({...style,yScale:value}))} ariaLabel="Y 轴标尺"/></div>{(["xMin","xMax","yMin","yMax"] as const).map(key=><div className="field" key={key}><label htmlFor={`chart-${key}`}>{({xMin:"X 最小值",xMax:"X 最大值",yMin:"Y 最小值",yMax:"Y 最大值"} as const)[key]}</label><input id={`chart-${key}`} className="input" type="number" value={chartStyle[key]} onChange={event=>setChartStyle(style=>({...style,[key]:event.target.value}))} placeholder="自动"/></div>)}</div><label className="chart-toggle-row"><input type="checkbox" checked={chartStyle.showMinorTicks} onChange={event=>setChartStyle(style=>({...style,showMinorTicks:event.target.checked}))}/><span>显示次刻度</span></label></fieldset>
                  <fieldset><legend>图例与视觉</legend><div className="chart-style-grid"><div className="field"><label htmlFor="chart-legend">图例位置</label><TranslatedSelect id="chart-legend" value={chartStyle.legendPosition} options={[{value:"auto",label:"自动",meaning:"根据图形内容选择占用最少的位置"},{value:"right",label:"右侧",meaning:"图例放在绘图区右侧外部"},{value:"left",label:"左侧",meaning:"图例放在绘图区左侧外部"},{value:"top",label:"顶部",meaning:"图例水平放在图表上方"},{value:"bottom",label:"底部",meaning:"图例水平放在图表下方"},{value:"inside_upper_right",label:"右上角内部",meaning:"图例位于绘图区右上角"},{value:"none",label:"隐藏",meaning:"不显示图例，适合直接标注或单系列图"}]} onChange={value=>setChartStyle(style=>({...style,legendPosition:value}))} ariaLabel="图例位置"/></div><div className="field"><label htmlFor="chart-palette">配色</label><TranslatedSelect id="chart-palette" value={chartStyle.palette} options={[{value:"nature",label:"Nature",meaning:"深蓝、绿色与克制强调色的投稿配色"},{value:"office",label:"Microsoft Office",meaning:"与 Office 默认主题相近，迁移到办公文档更协调"},{value:"colorblind",label:"色觉友好",meaning:"避免只依赖红绿差异区分类别"},{value:"grayscale",label:"灰度打印",meaning:"适合黑白打印与审稿材料"}]} onChange={value=>setChartStyle(style=>({...style,palette:value}))} ariaLabel="图表配色"/></div><div className="field"><label htmlFor="chart-line-width">线宽（pt）</label><input id="chart-line-width" className="input" type="number" min="0.5" max="4" step="0.25" value={chartStyle.lineWidth} onChange={event=>setChartStyle(style=>({...style,lineWidth:Number(event.target.value)}))}/></div><div className="field"><label htmlFor="chart-marker">数据标记</label><TranslatedSelect id="chart-marker" value={chartStyle.marker} options={[{value:"auto",label:"自动圆点",meaning:"折线或散点默认使用圆形标记"},{value:"circle",label:"圆形",meaning:"使用圆形标记数据点"},{value:"square",label:"方形",meaning:"使用方形标记数据点"},{value:"triangle",label:"三角形",meaning:"使用三角形标记数据点"},{value:"none",label:"不显示",meaning:"不绘制点标记，仅保留线或其他几何图形"}]} onChange={value=>setChartStyle(style=>({...style,marker:value}))} ariaLabel="数据标记"/></div></div><label className="chart-toggle-row"><input type="checkbox" checked={chartStyle.showDataLabels} onChange={event=>setChartStyle(style=>({...style,showDataLabels:event.target.checked}))}/><span>显示数值标签（密集图建议关闭）</span></label></fieldset>
                </div></details>
                <details className="chart-style-editor"><summary>投稿统计说明（导出前填写）</summary><fieldset className="statistics-fields"><legend>投稿级统计信息</legend><div className="field"><label htmlFor="independent-unit">独立样本单位 <TranslatedTerm term="n" meaning="独立样本数量或独立实验单位"/></label><input id="independent-unit" className="input" value={independentUnit} onChange={event=>setIndependentUnit(event.target.value)} placeholder="例如：3 批独立合成材料"/></div><div className="field"><label htmlFor="error-bar">误差线 / 不确定性</label><TranslatedSelect id="error-bar" value={errorBar} options={[{value:"",label:"尚未说明",meaning:errorBarMeanings[""]},{value:"ci95",label:"95% 置信区间",meaning:errorBarMeanings.ci95},{value:"sd",label:"标准差 SD",meaning:errorBarMeanings.sd},{value:"se",label:"标准误 SE",meaning:errorBarMeanings.se},{value:"none",label:"不适用（展示原始点/分布）",meaning:errorBarMeanings.none}]} onChange={setErrorBar} ariaLabel="误差线 / 不确定性"/></div><div className="field"><label htmlFor="statistical-test">统计检验或不适用理由</label><input id="statistical-test" className="input" value={statisticalTest} onChange={event=>setStatisticalTest(event.target.value)} placeholder="例如：双侧 Welch t 检验；或说明无需检验"/></div></fieldset></details>{chartWarnings.length ? <div className="chart-warning-list" role="status"><strong>投稿导出前需补充</strong>{chartWarnings.map(warning=><span key={warning}>{warning}</span>)}</div> : chartPreview ? <div className="chart-ready"><CheckCircle2 size={15}/><span>统计信息完整，可进行安全导出。</span></div> : null}{chartPreview && <><Image unoptimized width={640} height={360} className="chart-thumb" src={chartPreview} alt="生成的科研图预览"/><div className="chart-downloads">{["svg","pdf","png","tiff","xlsx","package"].filter(format=>chartDownloads[format]).map(format=><TranslationTooltip term={format==="package"?"图表包":format.toUpperCase()} meaning={formatMeanings[format]} key={format}><a className="button secondary compact" href={chartDownloads[format]} download>{format==="package"?"图表包":format.toUpperCase()}</a></TranslationTooltip>)}</div></>}<button className="button primary" onClick={createChart}><BarChart3 size={15}/>生成图表</button></div></Tabs.Content>
              <Tabs.Content value="compose">
                <div className="composition-contract"><Sparkles size={14}/><span>组图将按当前顺序生成真实 <TranslatedTerm term="SVG" meaning={formatMeanings.svg}/>、<TranslatedTerm term="PDF" meaning={formatMeanings.pdf}/>、<TranslatedTerm term="PNG" meaning={formatMeanings.png}/> 与 <TranslatedTerm term="TIFF" meaning={formatMeanings.tiff}/>；数据图保持完整坐标轴，图片素材使用中心裁切。</span></div>
                <div className={`composer-grid template-${compositionTemplate}`} style={{gap:compositionGap}}>
                  {compositionItems.length ? compositionItems.map((panel,index)=><div className="composer-cell filled" draggable key={panel.key} onDragStart={event=>event.dataTransfer.setData('text/panel-id',panel.key)} onDragOver={event=>event.preventDefault()} onDrop={event=>{event.preventDefault();reorderPanel(event.dataTransfer.getData('text/panel-id'),panel.key)}}>
                    <div className="composer-panel-visual">{panel.previewUrl ? <Image unoptimized width={360} height={220} src={panel.previewUrl} alt={panel.title}/> : <ImageIcon size={28}/>}<strong>{String.fromCharCode(97+index)}</strong><em>{panel.kind==='chart'?'数据图':'开放素材'}</em></div>
                    <span>{panel.title}</span>
                    {panel.kind==='asset' ? <label className="panel-zoom" onPointerDown={event=>event.stopPropagation()}>中心裁切 · {Math.round((panelZoom[panel.key] || 1)*100)}%<input type="range" min="100" max="220" value={(panelZoom[panel.key] || 1)*100} onChange={event=>setPanelZoom(values=>({...values,[panel.key]:Number(event.target.value)/100}))}/></label> : <small>保持完整坐标轴，不执行裁切</small>}
                  </div>) : <><div className="composer-cell">A · 接受开放许可素材</div><div className="composer-cell">B · 或先生成数据图</div></>}
                  {Array.from({length:Math.max(0,({"1x2":2,"2x2":4,"hero_side":4,"vertical":6}[compositionTemplate])-(compositionItems.length || 2))}).map((_,index)=><div className="composer-cell" key={`empty-${index}`}><Plus size={18}/><span>空面板</span></div>)}
                </div>
                <div className="composition-settings">
                  <div className="field"><label htmlFor="composition-template">组图模板</label><TranslatedSelect id="composition-template" value={compositionTemplate} options={[{value:"1x2",label:"1 × 2",meaning:"一行两列：适合并列比较两个面板"},{value:"2x2",label:"2 × 2",meaning:"两行两列：适合最多四个等权面板"},{value:"hero_side",label:"主图＋辅图",meaning:"左侧主图配右侧多个辅助面板"},{value:"vertical",label:"纵向堆叠",meaning:"从上到下排列，适合流程或长图"}]} onChange={value=>setCompositionTemplate(value as typeof compositionTemplate)} ariaLabel="组图模板"/></div>
                  <div className="field"><label htmlFor="composition-aspect">画布比例</label><TranslatedSelect id="composition-aspect" value={compositionAspect} options={[{value:"auto",label:"随模板自动",meaning:"根据模板和面板数量自动计算画布比例"},{value:"1:1",label:"1 : 1",meaning:"正方形画布：宽度与高度相等"},{value:"4:3",label:"4 : 3",meaning:"标准横向比例：适合常规科研图"},{value:"16:9",label:"16 : 9",meaning:"宽屏横向比例：适合较宽的流程或时间序列"}]} onChange={value=>setCompositionAspect(value as typeof compositionAspect)} ariaLabel="画布比例"/></div>
                  <div className="field"><label htmlFor="composition-width">投稿版宽（<TranslatedTerm term="mm" meaning="毫米：期刊栏宽常用的物理尺寸单位"/>）</label><TranslatedSelect id="composition-width" value={String(compositionWidth)} options={[{value:"89",label:"单栏 · 89 mm",meaning:"Nature 常见单栏宽度，适合一个主要面板或紧凑图"},{value:"180",label:"双栏 · 180 mm",meaning:"跨双栏宽度，适合复杂多面板图"}]} onChange={value=>setCompositionWidth(Number(value) as 89|180)} ariaLabel="投稿版宽"/></div>
                  <div className="field"><label htmlFor="composition-dpi"><TranslatedTerm term="TIFF" meaning={formatMeanings.tiff}/> 分辨率（<TranslatedTerm term="dpi" meaning="每英寸点数：衡量印刷输出分辨率"/>）</label><TranslatedSelect id="composition-dpi" value={String(compositionDpi)} options={[{value:"300",label:"300 dpi",meaning:"常规彩色或灰度印刷分辨率"},{value:"600",label:"600 dpi",meaning:"高分辨率投稿输出，适合线条、文字和精细图件"}]} onChange={value=>setCompositionDpi(Number(value) as 300|600)} ariaLabel="TIFF 分辨率"/></div>
                </div>
                <div className="field composition-gap"><label htmlFor="panel-gap">面板间距 · {compositionGap} <TranslatedTerm term="px" meaning="像素：屏幕界面中的长度单位"/></label><input id="panel-gap" type="range" min="0" max="24" value={compositionGap} onChange={event=>setCompositionGap(Number(event.target.value))}/></div>
                {compositionWarnings.length ? <div className="chart-warning-list"><strong>组图导出警告</strong>{compositionWarnings.map(warning=><span key={warning}>{warning}</span>)}</div> : null}
                {compositionPreview && <div className="composition-output"><Image unoptimized width={900} height={620} src={compositionPreview} alt="投稿级多面板组图预览"/><div className="composition-downloads">{Object.entries(compositionDownloads).map(([format,url])=><TranslationTooltip term={format.toUpperCase()} meaning={formatMeanings[format] || "图像导出格式"} key={format}><a className="button secondary compact" href={url} download>{format.toUpperCase()}</a></TranslationTooltip>)}</div></div>}
                <button className="button primary composition-save" disabled={compositionSaving || !compositionItems.length} onClick={saveComposition}><Grid2X2 size={15}/>{compositionSaving?'正在渲染四种格式…':'生成投稿级组图'}</button>
              </Tabs.Content>
              <Tabs.Content value="caption"><div className="field"><label htmlFor="caption">图题与图注草稿</label><textarea id="caption" className="input caption-area" value={compositionCaption} onChange={event=>setCompositionCaption(event.target.value)} placeholder="说明核心结论、各面板含义、样本量、误差线与统计检验。生成组图后将随锚点写入文档。"/></div><button className="button primary" style={{width:'100%',marginTop:10}} onClick={()=>{setActiveTab('compose');setToast('图注已保留；生成组图时将创建检查点并用于文档回填')}}><Check size={15}/>确认并返回组图</button></Tabs.Content>
              <Tabs.Content value="source"><div className="context-card"><span className="badge safe">当前列表 · {candidates.filter(item=>item.citationSelected).length} 条已引用 · {acceptedCount} 张已选图片</span><p>引用后，编号立即显示在正文段尾。未选中的检索结果无需逐条审批。图片加入组图前才核验复用许可。</p><details><summary>候选处理统计</summary><p>{pendingRejectCount} 项未选，已拒绝 {rejectedCandidateCount} 项，{unresolvedLicenseCount} 项图片许可待核验。</p></details></div><button className="button secondary" style={{width:'100%'}} onClick={()=>setSourceListOpen(true)}><Files size={15}/>查看完整来源清单</button></Tabs.Content>
            </div></Tabs.Root></aside>}
        </section> : <section className="management-page">
          {screen === "library" && <><input ref={packageInput} type="file" accept=".zip,.nfw-project.zip,application/zip" hidden onChange={importProjectPackage}/><div className="management-heading"><div><div className="eyebrow"><TranslatedTerm term="Projects" meaning="项目库"/></div><h1>项目库</h1><p>继续最近任务，或导入、导出可迁移到另一台电脑的便携项目包。</p></div><div className="management-heading-actions"><button className="button secondary" onClick={()=>packageInput.current?.click()}><Upload size={15}/>导入便携项目包</button><button className="button primary" onClick={()=>{setScreen('home');setToast('请从首页选择或拖入文件')}}><Plus size={15}/>新建项目</button></div></div><div className="management-list">{projects.length ? projects.map(project=><article className="management-card" key={project.id}><div><span className="badge safe">{project.quality_mode === 'balanced' ? '平衡模式' : '质量优先'}</span><h3>{project.name}</h3><p>{project.status} · {project.progress}%</p></div><div className="management-actions"><button className="button secondary" aria-label={`${project.name} 切换为${project.quality_mode === 'balanced' ? '质量优先' : '平衡模式'}`} onClick={()=>toggleProjectQualityMode(project)}><ShieldCheck size={14}/>{project.quality_mode === 'balanced' ? '使用质量优先' : '使用平衡模式'}</button><button className="button secondary" onClick={()=>exportProjectPackage(project)}><Download size={14}/>导出便携包</button><button className="button secondary" onClick={()=>loadProject(project.id).catch(()=>setToast('项目读取失败'))}>打开</button><button className="button danger" onClick={()=>trashProject(project.id)}>移入回收站</button></div></article>) : <div className="empty-note">还没有真实项目。请从首页上传文档、数据文件或导入便携项目包。</div>}</div></>}
          {screen === "trash" && <><div className="management-heading"><div><div className="eyebrow"><TranslatedTerm term="Recovery" meaning="恢复与找回"/></div><h1>30 天回收站</h1><p>项目可在保留期内恢复；只有超过 30 天的项目才会被到期清理。</p></div><button className="button danger" onClick={purgeExpiredTrash}><Trash2 size={15}/>清理到期项目</button></div><div className="management-list">{trashProjects.length ? trashProjects.map(project=><article className="management-card" key={project.id}><div><span className="badge warn">回收站</span><h3>{project.name}</h3><p>移入时间：{project.trashed_at ? new Date(project.trashed_at).toLocaleString('zh-CN') : '未知'}</p></div><div className="management-actions"><button className="button secondary" onClick={()=>restoreProject(project.id)}>恢复项目</button></div></article>) : <div className="empty-note">回收站为空。</div>}</div></>}
          {screen === "settings" && <>
            <div className="management-heading"><div><div className="eyebrow"><TranslatedTerm term="Local configuration" meaning="本地配置"/></div><h1>设置</h1><p>模型密钥保存到 Windows 凭据管理器；同一时间最多启用一个模型。</p></div></div>
            <div className="settings-grid">
              <section className="settings-card account-settings-card">
                <div className="account-settings-head"><div><h2>本机账户</h2><p><strong>{account.display_name}</strong> · {account.username}</p></div><button className="button secondary compact" onClick={logoutAccount}>退出登录</button></div>
                {account.must_change_password && <div className="notice account-warning"><CircleAlert size={16}/><span>当前仍在使用开发初始密码，请修改后再保存正式科研资料。</span></div>}
                <div className="field"><label htmlFor="current-account-password">当前密码</label><input id="current-account-password" className="input" type="password" autoComplete="current-password" value={currentPassword} onChange={event=>setCurrentPassword(event.target.value)}/></div>
                <div className="field"><label htmlFor="new-account-password">新密码（至少 8 个字符）</label><input id="new-account-password" className="input" type="password" autoComplete="new-password" value={newPassword} onChange={event=>setNewPassword(event.target.value)}/></div>
                <button className="button primary" disabled={passwordBusy || !currentPassword || newPassword.length<8} onClick={changeAccountPassword}>{passwordBusy?'正在更新…':'修改账户密码'}</button>
                <p className="settings-help">账户当前保存英文注解偏好与最近页面；模型密钥仍只保存在 Windows 凭据管理器。</p>
              </section>
              <section className="settings-card model-settings-card">
                <h2>新增 <TranslatedTerm term="OpenAI-compatible" meaning="兼容 OpenAI 接口格式的模型服务"/> 模型</h2>
                <p className="settings-help"><strong><TranslatedTerm term="Base URL" meaning="接口服务的根地址"/></strong> 是 <TranslatedTerm term="API" meaning="应用程序编程接口：网页与模型服务通信的约定"/> 根地址；<strong>模型 <TranslatedTerm term="ID" meaning="标识符：服务端用于区分具体模型的名称"/></strong> 必须与服务端 <TranslatedTerm term="/models" meaning="用于列出服务端可用模型的接口路径"/> 返回值一致；<strong><TranslatedTerm term="API Key" meaning="访问在线模型服务所需的秘密凭据"/></strong> 是在线服务凭据。本地服务可不填密钥。</p>
                <div className="model-kind-switch" role="group" aria-label="模型服务类型"><button className={modelKind==='local'?'active':''} onClick={()=>{setModelKind('local');setModelBaseUrl('http://127.0.0.1:1234/v1')}}><Server size={14}/>本地模型</button><button className={modelKind==='online'?'active':''} onClick={()=>{setModelKind('online');setModelBaseUrl('')}}><Cloud size={14}/>在线模型</button></div>
                <div className="field"><label htmlFor="model-profile-name">配置名称</label><input id="model-profile-name" className="input" value={modelProfileName} onChange={event=>setModelProfileName(event.target.value)} placeholder="例如：DeepSeek V4 Pro / 本地 Qwen"/></div>
                <div className="field"><label htmlFor="model-base"><TranslatedTerm term="Base URL" meaning="模型接口服务的根地址，例如 https://api.example.com"/></label><input id="model-base" className="input" value={modelBaseUrl} onChange={event=>setModelBaseUrl(event.target.value)} placeholder={modelKind==='local'?'http://127.0.0.1:1234/v1':'https://api.deepseek.com'}/><small>{modelKind==='local'?'仅允许 localhost、127.0.0.1 或 ::1，兼容 Ollama、LM Studio 等 OpenAI 接口。':'在线地址强制使用 HTTPS；DeepSeek 官方地址为 https://api.deepseek.com。'}</small></div>
                <div className="field"><label htmlFor="model-name">模型 <TranslatedTerm term="ID" meaning="模型标识符：必须与服务端返回的实际名称完全一致"/></label><input id="model-name" className="input" value={modelName} onChange={event=>setModelName(event.target.value)} placeholder={modelKind==='online'?'deepseek-v4-pro':'服务端实际模型 ID'}/></div>
                <div className="field"><label htmlFor="api-key"><TranslatedTerm term="API Key" meaning="访问在线接口的秘密凭据，仅写入 Windows 凭据管理器"/> {modelKind==='local'?'（可选）':'（必填）'}</label><input id="api-key" className="input" type="password" value={apiKey} onChange={event=>setApiKey(event.target.value)} autoComplete="new-password" placeholder={modelKind==='local'?'本地服务通常留空':'仅写入 Windows 凭据管理器'}/></div>
                <button className="button primary" onClick={saveModelSettings}>保存到模型列表</button>
                <div className="model-profile-section"><div className="model-profile-heading"><strong>已保存模型</strong><span>{modelProfiles.filter(profile=>profile.enabled).length ? '1 个已启用' : '全部停用'}</span></div>{modelProfiles.length ? <div className="model-profile-list">{modelProfiles.map(profile=><article className={`model-profile ${profile.enabled?'enabled':''}`} key={profile.id}><div className="model-profile-icon">{profile.kind==='local'?<Server size={16}/>:<Cloud size={16}/>}</div><div className="model-profile-copy"><strong>{profile.name}</strong><span>{profile.model}</span><small>{profile.base_url}</small><em>{profile.enabled?'当前启用':profile.ready?'已就绪':'缺少凭据'}</em></div><div className="model-profile-actions"><button className={`model-toggle ${profile.enabled?'on':''}`} role="switch" aria-checked={profile.enabled} aria-label={`${profile.enabled?'停用':'启用'} ${profile.name}`} onClick={()=>toggleModelProfile(profile)}><Power size={14}/><span>{profile.enabled?'停用':'启用'}</span></button><button className="icon-btn" aria-label={`删除 ${profile.name}`} onClick={()=>deleteModelProfile(profile)}><Trash2 size={14}/></button></div></article>)}</div> : <div className="empty-note">尚未保存模型。未启用模型时，智能助手只使用本地规则。</div>}</div>
              </section>
              <section className="settings-card storage-settings-card">
                <h2>项目存储</h2>
                <p>先浏览并选择目标文件夹，再单独确认是否迁移。选择文件夹本身不会移动或删除任何项目。</p>
                <div className="field"><label htmlFor="projects-root">项目根目录</label><input id="projects-root" className="input" value={projectsRoot} onChange={event=>setProjectsRoot(event.target.value)}/></div>
                <div className="storage-action-groups">
                  <div className="storage-action browse"><span>选择位置</span><button className="button secondary" disabled={folderPickerBusy} onClick={browseProjectsRoot}><FolderOpen size={15}/>{folderPickerBusy?'等待文件夹选择…':'浏览本地文件夹'}</button></div>
                  <div className="storage-action migrate"><span>应用变更</span><button className="button primary" onClick={migrateProjectsRoot}><Archive size={15}/>应用并迁移现有项目</button></div>
                </div>
                <div className="notice storage-notice"><ShieldCheck size={16}/><span>迁移前会检查路径、权限、嵌套目录和重名冲突；确认前当前位置保持不变。</span></div>
              </section>
              <section className="settings-card"><h2>显示、更新与隐私</h2><div className="annotation-setting"><div><strong>英文注解</strong><span>控制全站英文术语悬停翻译及选项中文解释栏；关闭后不会改变字段值。</span></div><button className={`model-toggle ${annotationsEnabled?'on':''}`} role="switch" aria-checked={annotationsEnabled} aria-label="英文注解总开关" onClick={toggleAnnotations}><Power size={14}/><span>{annotationsEnabled?'已开启':'已关闭'}</span></button></div><div className="update-setting"><div className="notice"><ShieldCheck size={16}/><span>{updateMessage || '未配置发布源；应用不会自动联网更新。'}</span></div><button className="button secondary" disabled={!updateStatus.configured || updateChecking} onClick={checkForUpdates}><RotateCcw size={14}/>{updateChecking?'正在检查…':'手动检查更新'}</button></div>{updateResult?.update_available && updateResult.release_url ? <a className="button primary update-release-link" href={updateResult.release_url} target="_blank" rel="noreferrer"><ExternalLink size={14}/>打开新版本发布页</a> : null}<p>当前版本：{updateStatus.current_version || '未知'}。应用不会自动联网；只有点击“手动检查更新”才读取发布清单。服务仅监听 127.0.0.1；正文不会进入日志。</p></section>
            </div>
          </>}
        </section>}
      </main>
    </div>

    {!assistantOpen && <button className="assistant-launcher" onClick={openAssistant} aria-label="打开学术助手"><MessageCircle size={18}/><span>学术助手</span></button>}
    {assistantOpen && <AcademicAssistant key={currentProject?.id||"new"} project={currentProject} anchor={anchors.find(a=>a.text===assistantSelection)} selectedText={assistantSelection} initialTopic={currentProject?.name||topic} onDirtyChange={setAssistantDirty} onClose={()=>setAssistantOpen(false)}/>}
    {readerOpen&&currentProject&&<ReaderPanel projectId={currentProject.id} onClose={()=>setReaderOpen(false)} onSelect={text=>{setAssistantSelection(text);setReaderOpen(false);setAssistantOpen(true)}}/>}
    <Dialog.Root open={jobsOpen} onOpenChange={setJobsOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay"/>
        <Dialog.Content className="dialog-content task-center-dialog" aria-describedby="task-center-desc">
          <div className="task-center-header"><div><div className="eyebrow"><TranslatedTerm term="Persistent queue" meaning="持久化任务队列：关闭页面或重启后任务状态仍保留"/></div><Dialog.Title asChild><h2>任务中心</h2></Dialog.Title><Dialog.Description id="task-center-desc">解析/<TranslatedTerm term="OCR" meaning="光学字符识别：从扫描页提取可编辑文字"/>、制图、组图与导出只会在阶段边界暂停或取消；关闭网页或重启应用后，未完成任务仍保存在 <TranslatedTerm term="SQLite" meaning="本机嵌入式数据库，不需要单独数据库服务"/> 队列中。</Dialog.Description></div><Dialog.Close asChild><button className="icon-btn" aria-label="关闭任务中心"><X size={16}/></button></Dialog.Close></div>
          <div className="task-summary" aria-live="polite"><span><strong>{jobs.filter(job=>['queued','running','paused'].includes(job.state)).length}</strong> 进行中</span><span><strong>{jobs.filter(job=>job.state==='failed').length}</strong> 失败</span><span><strong>{jobs.filter(job=>job.state==='completed').length}</strong> 已完成</span><em>{jobsLoading?'正在连接…':'实时连接 · 断线自动轮询'}</em></div>
          <div className="task-list">
            {!jobs.length && !jobsLoading ? <div className="empty-note">尚无后台任务。上传文档后，解析与 OCR 任务会显示在这里。</div> : jobs.map(job=><article className={`task-card state-${job.state}`} key={job.id}>
              <div className="task-card-head"><div><span className={`task-state ${job.state}`}>{{queued:'等待',running:'运行中',paused:'已暂停',completed:'已完成',failed:'失败',cancelled:'已取消'}[job.state]}</span><h3>{jobKindLabels[job.kind] || job.kind}</h3><p>{job.project_name || job.project_id}</p></div><time>{new Date(job.updated_at).toLocaleString('zh-CN')}</time></div>
              <div className="task-stage"><span>{job.stage}</span><strong>{job.progress}%</strong></div>
              <div className="task-progress" role="progressbar" aria-label={`${job.stage} ${job.progress}%`} aria-valuemin={0} aria-valuemax={100} aria-valuenow={job.progress}><span style={{width:`${job.progress}%`}}/></div>
              {job.message && <p className="task-message">{job.message}</p>}
              <div className="task-actions">
                {(job.state==='queued'||job.state==='running') && <><button className="button secondary compact" onClick={()=>controlJob(job,'pause')}><Pause size={13}/>暂停</button><button className="button secondary compact" onClick={()=>controlJob(job,'cancel')}><X size={13}/>取消</button></>}
                {job.state==='paused' && <><button className="button primary compact" onClick={()=>controlJob(job,'resume')}><Play size={13}/>恢复</button><button className="button secondary compact" onClick={()=>controlJob(job,'cancel')}><X size={13}/>取消</button></>}
                {(job.state==='failed'||job.state==='cancelled') && <button className="button secondary compact" onClick={()=>controlJob(job,'retry')}><RotateCcw size={13}/>重试</button>}
              </div>
            </article>)}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>

    <Dialog.Root open={documentEditorOpen} onOpenChange={setDocumentEditorOpen}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay"/>
        <Dialog.Content className="dialog-content full-document-dialog" aria-describedby="document-edit-desc" onKeyDown={event=>{if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='s'){event.preventDefault();saveFullDocument()}}}>
          <div className="full-document-header"><div><Dialog.Title asChild><h2>编辑全文</h2></Dialog.Title><Dialog.Description id="document-edit-desc">一次查看并修改全部章节。保存会形成一项完整修订，可整体撤销；后台段落锚点只用于配图和页码映射。</Dialog.Description></div><Dialog.Close asChild><button className="icon-btn" aria-label="关闭全文编辑器"><X size={16}/></button></Dialog.Close></div>
          <div className="full-document-stats"><span>{documentDrafts.length} 个锚点段落</span><span>{documentCharacterCount.toLocaleString('zh-CN')} 字符</span><span className={changedDocumentCount?'changed':'muted'}>{!documentStructureValid ? '段落边界待修复' : changedDocumentCount ? `${changedDocumentCount} 段已修改` : '尚无修改'}</span></div>
          <div className="document-tools" aria-label="全文查找替换">
            <div className="document-tool-group">
              <div className="field"><label htmlFor="document-find">查找</label><input id="document-find" className="input" value={documentFind} onChange={event=>{setDocumentFind(event.target.value);setDocumentFindCursor(-1)}} onKeyDown={event=>{if(event.key==='Enter'){event.preventDefault();focusDocumentMatch(event.shiftKey?-1:1)}}} placeholder="全文查找"/></div>
              <div className="document-tool-actions">
                <button className="button secondary compact" disabled={!documentFindMatches.length} onClick={()=>focusDocumentMatch(-1)}><ChevronUp size={14}/>上一个</button>
                <button className="button secondary compact" onClick={()=>focusDocumentMatch(1)}>下一个<ChevronDown size={14}/></button>
                <span className="document-match-count" role="status">{documentFind ? (documentFindMatches.length ? `${documentFindCursor+1}/${documentFindMatches.length} 处` : '0 处') : 'Enter 查找'}</span>
              </div>
            </div>
            <div className="document-tool-group">
              <div className="field"><label htmlFor="document-replace">替换为</label><input id="document-replace" className="input" value={documentReplace} onChange={event=>setDocumentReplace(event.target.value)} placeholder="替换文字，可留空"/></div>
              <div className="document-tool-actions replace-actions"><button className="button secondary compact" onClick={replaceAcrossDocument}>替换全文</button></div>
            </div>
          </div>
          {documentDrafts.some(draft=>draft.page) && <div className="notice document-edit-notice"><CircleAlert size={16}/><span>PDF 原文件不会被原位覆盖；保存的是全文工作区修订，导出时生成修订稿和页码/坐标报告。</span></div>}
          <div className="full-document-outline" aria-label="原文章节顺序">{documentDrafts.filter((draft,index)=>index===0 || documentDrafts[index-1].section!==draft.section).map(draft=><span key={`${draft.id}-${draft.section}`}>{draft.section || '未命名章节'}</span>)}</div>
          <div className="full-document-editor single-document-editor"><label className="sr-only" htmlFor="full-document-text">整篇文档正文</label><textarea ref={fullDocumentTextareaRef} id="full-document-text" value={documentFullText} onChange={event=>{setDocumentFullText(event.target.value);setDocumentFindCursor(-1)}} spellCheck className="full-document-surface" aria-describedby="full-document-structure-help"/></div>
          <div id="full-document-structure-help" className={`document-structure-help ${documentStructureValid?'valid':'invalid'}`}>{documentStructureValid ? '整篇连续编辑已启用。空行用于保持原段落锚点，保存时统一写入全部修改。' : `当前识别到 ${documentParagraphTexts.length} 段，原文需要 ${documentDrafts.length} 段。请调整空行后再保存，以免图件锚点错位。`}</div>
          <div className="full-document-footer"><div className="field"><label htmlFor="document-edit-reason">本次全文修改理由（写入审计）</label><input id="document-edit-reason" className="input" value={documentEditReason} onChange={event=>setDocumentEditReason(event.target.value)} placeholder="例如：统一术语、修正结果描述并补充实验条件"/></div><div className="full-document-actions"><span>Ctrl/⌘ + S 保存</span><Dialog.Close asChild><button className="button secondary">取消</button></Dialog.Close><button className="button primary" disabled={documentSaving || !documentStructureValid || !changedDocumentCount || documentEditReason.trim().length<3} onClick={saveFullDocument}><Check size={15}/>{documentSaving?'正在保存…':`保存全文修订${changedDocumentCount?`（${changedDocumentCount} 段）`:''}`}</button></div></div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>

    <Dialog.Root open={sourceListOpen} onOpenChange={setSourceListOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content source-list-dialog"><Dialog.Title>完整来源清单</Dialog.Title><Dialog.Description>核对文献题录与原始链接。已引用条目随项目导出。</Dialog.Description><div className="source-audit-list">{candidates.map(c=><article key={c.id}><strong>{c.title}</strong><p>{c.author||"作者待核对"} · {c.source}{c.year?" · "+c.year:""}{c.journal?" · "+c.journal:""}</p>{c.doi&&<p>DOI：{c.doi}</p>}<p>{c.citationSelected?"已引用":"未引用"} · 访问时间：{formatAccessedAt(c.accessedAt)}</p>{c.sourceUrl&&<a href={c.sourceUrl} target="_blank" rel="noreferrer">打开原始来源</a>}<button className="button secondary compact" disabled={isDemoCandidateId(c.id)} onClick={()=>verifyReference(c.id)}>核对引用</button>{citationChecks[c.id]&&<p role="status">{citationChecks[c.id]}</p>}</article>)}</div>{!candidates.length&&<p>当前段落还没有来源候选。</p>}<div className="dialog-actions"><Dialog.Close asChild><button className="button primary">完成</button></Dialog.Close></div></Dialog.Content></Dialog.Portal></Dialog.Root>

    <Dialog.Root open={bulkRejectOpen} onOpenChange={open=>{if(!bulkRejectBusy)setBulkRejectOpen(open)}}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content" aria-describedby="bulk-reject-desc"><Dialog.Title asChild><h2>批量拒绝未选择候选</h2></Dialog.Title><Dialog.Description id="bulk-reject-desc">将为当前段落的 {pendingRejectCount} 项待处理候选写入拒绝记录。已引用来源与已接受图片会保留，操作完成后自动建立一个检查点。</Dialog.Description><Dialog.Close asChild><button className="icon-btn dialog-close" aria-label="关闭批量拒绝确认"><X size={16}/></button></Dialog.Close><div className="notice"><CircleAlert size={16}/><span>被拒绝项会从候选卡片区隐藏，但仍保留在完整来源清单和审计记录中。</span></div><div className="dialog-actions"><Dialog.Close asChild><button className="button secondary" disabled={bulkRejectBusy}>取消</button></Dialog.Close><button className="button primary" disabled={bulkRejectBusy || pendingRejectCount===0} onClick={rejectPendingCandidates}>{bulkRejectBusy?'保存中…':`确认拒绝 ${pendingRejectCount} 项`}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>

    <Dialog.Root open={!!editingCandidate} onOpenChange={open=>{if(!open)setEditingCandidate(null)}}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content source-edit-dialog" aria-describedby="source-edit-desc"><Dialog.Title asChild><h2>人工修改与核验图片来源</h2></Dialog.Title><Dialog.Description id="source-edit-desc">只修改元数据会把候选重置为“需核验”；只有你打开原始作品页和许可页核对后，才能明确标记为可复用。</Dialog.Description><Dialog.Close asChild><button className="icon-btn dialog-close" aria-label="关闭"><X size={16}/></button></Dialog.Close><div className="field"><label htmlFor="source-title">标题</label><input id="source-title" className="input" value={sourceDraft.title} onChange={event=>setSourceDraft(draft=>({...draft,title:event.target.value,verified:false}))}/></div><div className="field"><label htmlFor="source-url">原始来源 URL</label><input id="source-url" className="input" type="url" value={sourceDraft.sourceUrl} onChange={event=>setSourceDraft(draft=>({...draft,sourceUrl:event.target.value,verified:false}))} placeholder="https://…"/></div><div className="field"><label htmlFor="source-author">作者 / 机构</label><input id="source-author" className="input" value={sourceDraft.author} onChange={event=>setSourceDraft(draft=>({...draft,author:event.target.value,verified:false}))}/></div><div className="field"><label htmlFor="source-license">许可声明</label><input id="source-license" className="input" value={sourceDraft.license} onChange={event=>setSourceDraft(draft=>({...draft,license:event.target.value,verified:false}))}/></div><div className="field"><label htmlFor="source-license-url">许可原文 URL</label><input id="source-license-url" className="input" type="url" value={sourceDraft.licenseUrl} onChange={event=>setSourceDraft(draft=>({...draft,licenseUrl:event.target.value,verified:false}))} placeholder="https://creativecommons.org/licenses/…"/></div><div className="field"><label htmlFor="source-reuse-status">复用结论</label><TranslatedSelect id="source-reuse-status" value={sourceDraft.reuseStatus} options={[{value:"unknown",label:"需核验",meaning:"来源或许可尚未核实，不进入最终资产"},{value:"citation_only",label:"仅作引用线索",meaning:"可以保留为引用来源，但不能自动复用图像"},{value:"attribution_required",label:"可复用并需署名",meaning:"许可允许复用，但最终图注必须保留作者和许可"},{value:"safe_open",label:"开放许可可复用",meaning:"已核对为无需额外限制的开放许可或公有领域"}]} onChange={value=>setSourceDraft(draft=>({...draft,reuseStatus:value as CandidateReuseStatus,verified:false}))} ariaLabel="素材复用结论"/></div><label className="chart-toggle-row source-verification"><input type="checkbox" checked={sourceDraft.verified} disabled={!sourceDraft.licenseUrl || !["safe_open","attribution_required"].includes(sourceDraft.reuseStatus)} onChange={event=>setSourceDraft(draft=>({...draft,verified:event.target.checked}))}/><span>我已打开原始作品页与许可原文，确认作者、许可和复用结论一致</span></label><div className="field"><label htmlFor="source-reason">修改或核验理由（写入审计）</label><textarea id="source-reason" className="input" value={sourceDraft.reason} onChange={event=>setSourceDraft(draft=>({...draft,reason:event.target.value}))} placeholder="说明核对了哪个许可页面、为何修正元数据"/></div><div className="notice"><BookOpenText size={16}/><span>引用文献请直接点“引用来源”。只有将图片加入组图，才需要核验图片许可。</span></div>{sourceIssues.length>0&&<ul className="source-edit-issues" aria-label="保存条件">{sourceIssues.map(issue=><li key={issue}>{issue}</li>)}</ul>}<div className="dialog-actions"><Dialog.Close asChild><button className="button secondary">取消</button></Dialog.Close><button className="button primary" disabled={sourceIssues.length>0} onClick={saveSourceEdit}><Check size={15}/>{sourceDraft.verified?'保存人工核验':'保存并标记待核验'}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>

    <Dialog.Root open={referenceOpen} onOpenChange={setReferenceOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content"><Dialog.Title>添加引用链接</Dialog.Title><Dialog.Description>从百度学术或原文页面复制来源信息。保存引用不会把论文图片加入组图。</Dialog.Description><div className="field"><label htmlFor="reference-title">标题</label><input id="reference-title" className="input" value={referenceDraft.title} onChange={e=>setReferenceDraft(d=>({...d,title:e.target.value}))}/></div><div className="field"><label htmlFor="reference-url">来源网址</label><input id="reference-url" type="url" className="input" value={referenceDraft.sourceUrl} onChange={e=>setReferenceDraft(d=>({...d,sourceUrl:e.target.value}))}/></div><div className="field"><label htmlFor="reference-author">作者（可选）</label><input id="reference-author" className="input" value={referenceDraft.author} onChange={e=>setReferenceDraft(d=>({...d,author:e.target.value}))}/></div><div className="dialog-actions"><Dialog.Close asChild><button className="button secondary">取消</button></Dialog.Close><button className="button primary" disabled={referenceSaving||!referenceDraft.title.trim()||!referenceDraft.sourceUrl.trim()} onClick={addReference}>{referenceSaving?"保存中…":"保存引用"}</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={privacyOpen} onOpenChange={setPrivacyOpen}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content"><Dialog.Title>本地敏感信息检查</Dialog.Title><Dialog.Description>当前段落的常见敏感字段扫描，尚未发送到在线模型。</Dialog.Description><div className="notice">{privacyPreview?.findings.length?"已标记："+privacyPreview.findings.join("、"):"未发现常见敏感字段，请自行核对。"}</div><pre className="privacy-preview">{privacyPreview?.redacted||displayParagraphs[selectedParagraph]}</pre><div className="dialog-actions"><Dialog.Close asChild><button className="button secondary">关闭</button></Dialog.Close><button className="button primary" onClick={()=>{setPrivacyOpen(false);openAssistant()}}>前往学术助手</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    <Dialog.Root open={exportOpen} onOpenChange={open=>{setExportOpen(open);if(!open)setExportOverrideReason("")}}><Dialog.Portal><Dialog.Overlay className="dialog-overlay"/><Dialog.Content className="dialog-content" aria-describedby="export-desc"><Dialog.Title asChild><h2>导出前统计检查</h2></Dialog.Title><Dialog.Description id="export-desc">先尝试安全导出。只要任一图表仍有统计阻止项，服务就不会生成投稿包；确需覆盖时必须填写理由并保留完整记录。</Dialog.Description><Dialog.Close asChild><button className="icon-btn dialog-close" aria-label="关闭"><X size={16}/></button></Dialog.Close>{projectChartBlockers.length ? <div className="notice export-blockers"><CircleAlert size={16}/><div><strong>项目内 {projectChartBlockers.length} 张图仍有 {projectChartBlockers.reduce((total,item)=>total+item.warnings.length,0)} 项统计阻止信息</strong>{projectChartBlockers.map(item=><span key={item.id}>{item.title}：{item.warnings.join("；")}</span>)}</div></div> : <div className="chart-ready"><CheckCircle2 size={16}/><span>项目内所有图表的统计字段均已完整，可尝试安全导出。</span></div>}<div className="context-card" style={{marginTop:12}}><div className="candidate-meta"><strong>已确认素材</strong><span>{acceptedCount} 项</span></div><div className="candidate-meta" style={{marginTop:8}}><strong>导出格式</strong><span><TranslatedTerm term="SVG" meaning={formatMeanings.svg}/> · <TranslatedTerm term="PDF" meaning={formatMeanings.pdf}/> · <TranslatedTerm term="TIFF" meaning={formatMeanings.tiff}/> · <TranslatedTerm term="PNG" meaning={formatMeanings.png}/></span></div><div className="candidate-meta" style={{marginTop:8}}><strong>文档输出</strong><span>新文档＋审计包</span></div></div><div className="field"><label htmlFor="export-override-reason">强制导出理由（仅覆盖风险时写入审计）</label><textarea id="export-override-reason" className="input" value={exportOverrideReason} onChange={event=>setExportOverrideReason(event.target.value)} placeholder="例如：该图仅展示原始观测分布，不进行推断统计；已由作者确认。"/></div><div className="dialog-actions export-actions"><button className="button secondary" onClick={()=>{setExportOpen(false);setActiveTab('chart')}}>返回修正</button><button className="button primary" disabled={exporting} onClick={()=>exportProject(false)}><Download size={15}/>{exporting?'正在检查…':'安全导出'}</button><button className="button danger" disabled={exporting || exportOverrideReason.trim().length<5} onClick={()=>exportProject(true)}><CircleAlert size={15}/>强制导出并留痕</button></div></Dialog.Content></Dialog.Portal></Dialog.Root>
    {toast && <div className="toast" role="status">{toast}</div>}
  </Tooltip.Provider></AnnotationContext.Provider>;
}
