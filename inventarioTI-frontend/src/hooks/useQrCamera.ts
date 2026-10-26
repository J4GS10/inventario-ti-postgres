import { useCallback, useEffect, useRef, useState } from 'react';

interface DetectedBarcode { rawValue: string }
interface Detector { detect: (source: HTMLVideoElement) => Promise<DetectedBarcode[]> }
type DetectorCtor = new (options?: { formats?: string[] }) => Detector;

/** Código del activo a partir de lo que trae el QR (texto, "asset:CÓDIGO" o URL). */
export function codigoDeQr(rawValue: string): string {
  const value = rawValue.trim();
  if (!value) return '';
  if (value.toLowerCase().startsWith('asset:')) return value.slice(6).trim();
  try {
    const url = new URL(value);
    return (url.pathname.split('/').filter(Boolean).pop() || value).trim();
  } catch {
    return value;
  }
}

export type EstadoCamara = 'idle' | 'active' | 'unsupported' | 'denied';

/**
 * Lectura continua de QR con la cámara (BarcodeDetector del navegador).
 * `onCode` recibe cada código distinto; un mismo código seguido se ignora
 * durante `pausaMs` para no registrarlo dos veces al sostener el equipo.
 */
export function useQrCamera(onCode: (codigo: string) => void | Promise<void>, pausaMs = 2500) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const frameRef = useRef<number | null>(null);
  const activoRef = useRef(false);
  const ocupadoRef = useRef(false);
  const ultimoRef = useRef<{ codigo: string; en: number }>({ codigo: '', en: 0 });
  const onCodeRef = useRef(onCode);
  const [estado, setEstado] = useState<EstadoCamara>('idle');

  useEffect(() => { onCodeRef.current = onCode; }, [onCode]);

  const detener = useCallback(() => {
    activoRef.current = false;
    if (frameRef.current !== null) window.cancelAnimationFrame(frameRef.current);
    frameRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
    setEstado((e) => (e === 'active' ? 'idle' : e));
  }, []);

  const iniciar = useCallback(async () => {
    const Ctor = (window as unknown as { BarcodeDetector?: DetectorCtor }).BarcodeDetector;
    if (!Ctor || !navigator.mediaDevices?.getUserMedia) {
      setEstado('unsupported');
      return;
    }
    try {
      const detector = new Ctor({ formats: ['qr_code'] });
      const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } }, audio: false });
      streamRef.current = stream;
      if (videoRef.current) {
        videoRef.current.srcObject = stream;
        await videoRef.current.play();
      }
      activoRef.current = true;
      setEstado('active');
      const tick = async () => {
        if (!activoRef.current || !videoRef.current) return;
        if (videoRef.current.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA && !ocupadoRef.current) {
          ocupadoRef.current = true;
          try {
            const raw = (await detector.detect(videoRef.current))[0]?.rawValue;
            const codigo = raw ? codigoDeQr(raw) : '';
            const ahora = Date.now();
            if (codigo && (codigo !== ultimoRef.current.codigo || ahora - ultimoRef.current.en > pausaMs)) {
              ultimoRef.current = { codigo, en: ahora };
              await onCodeRef.current(codigo);
            }
          } catch {
            // Un frame intermedio puede fallar; el siguiente reintenta.
          } finally {
            ocupadoRef.current = false;
          }
        }
        frameRef.current = window.requestAnimationFrame(tick);
      };
      frameRef.current = window.requestAnimationFrame(tick);
    } catch {
      setEstado('denied');
    }
  }, [pausaMs]);

  useEffect(() => detener, [detener]);

  return { videoRef, estado, iniciar, detener };
}
