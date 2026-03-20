import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:path/path.dart' as p;

void main() {
  runApp(const FlutterRecorderApp());
}

class FlutterRecorderApp extends StatelessWidget {
  const FlutterRecorderApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'flutterRecorder',
      debugShowCheckedModeBanner: false,
      theme: ThemeData(
        colorSchemeSeed: Colors.blueGrey,
        useMaterial3: true,
        brightness: Brightness.light,
      ),
      home: const RecorderPage(),
    );
  }
}

// ---------------------------------------------------------------------------
// Data model helpers
// ---------------------------------------------------------------------------

class DeviceInfo {
  final int index;
  final String name;
  DeviceInfo(this.index, this.name);
  @override
  String toString() => '[$index] $name';
}

class CameraFormat {
  final int width;
  final int height;
  final double fps;
  CameraFormat(this.width, this.height, this.fps);
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

class RecorderPage extends StatefulWidget {
  const RecorderPage({super.key});

  @override
  State<RecorderPage> createState() => _RecorderPageState();
}

class _RecorderPageState extends State<RecorderPage> {
  // Devices
  List<DeviceInfo> _videoDevices = [];
  List<DeviceInfo> _audioDevices = [];
  DeviceInfo? _selectedCamera;
  DeviceInfo? _selectedAudio;

  // Formats
  List<CameraFormat> _formats = [];
  List<String> _resolutions = [];
  List<String> _fpsOptions = [];
  String? _selectedResolution;
  String? _selectedFps;
  String _codec = 'h264';
  String _container = 'mov';
  double _crf = 20;
  double _preRoll = 5;

  // Output
  String _outputDir = '';
  String _baseName = 'recording';
  bool _burnTimecode = true;

  // Thermal
  String _thermalBackend = 'infiray';
  String _colormap = 'inferno';
  final TextEditingController _rangeMinCtrl = TextEditingController();
  final TextEditingController _rangeMaxCtrl = TextEditingController();

  // State
  String _status = 'Ready';
  bool _recording = false;
  int _clipCount = 0;
  Process? _recordProcess;
  Timer? _recordTimer;
  DateTime? _recordStart;

  // Python CLI path (sibling directory)
  String get _cliPath {
    // Resolve relative to this project's parent
    final projectDir = p.dirname(Platform.script.toFilePath());
    return p.join(p.dirname(projectDir), 'simpleRecorder', 'cli.py');
  }

  String get _simpleRecorderDir {
    final projectDir = p.dirname(Platform.script.toFilePath());
    return p.join(p.dirname(projectDir), 'simpleRecorder');
  }

  @override
  void initState() {
    super.initState();
    _outputDir = p.join(Platform.environment['HOME'] ?? '/tmp', 'Movies');
    _refreshDevices();
  }

  @override
  void dispose() {
    _recordTimer?.cancel();
    _rangeMinCtrl.dispose();
    _rangeMaxCtrl.dispose();
    super.dispose();
  }

  // -------------------------------------------------------------------------
  // Device enumeration via ffmpeg
  // -------------------------------------------------------------------------

  Future<void> _refreshDevices() async {
    setState(() => _status = 'Scanning devices...');

    try {
      final result = await Process.run(
        'ffmpeg',
        ['-f', 'avfoundation', '-list_devices', 'true', '-i', ''],
        stderrEncoding: const SystemEncoding(),
        stdoutEncoding: const SystemEncoding(),
      );

      // ffmpeg writes device list to stderr
      final stderr = result.stderr as String;
      final video = <DeviceInfo>[];
      final audio = <DeviceInfo>[];
      bool inVideo = false;
      bool inAudio = false;

      for (final line in stderr.split('\n')) {
        if (line.contains('AVFoundation video devices:')) {
          inVideo = true;
          inAudio = false;
          continue;
        }
        if (line.contains('AVFoundation audio devices:')) {
          inVideo = false;
          inAudio = true;
          continue;
        }

        // Match lines like: [AVFoundation ...] [0] FaceTime HD Camera
        final match = RegExp(r'\[(\d+)\]\s+(.+)').firstMatch(line);
        if (match != null) {
          final idx = int.parse(match.group(1)!);
          final name = match.group(2)!.trim();
          if (inVideo) {
            video.add(DeviceInfo(idx, name));
          } else if (inAudio) {
            audio.add(DeviceInfo(idx, name));
          }
        }
      }

      setState(() {
        _videoDevices = video;
        _audioDevices = audio;
        _selectedCamera = video.isNotEmpty ? video.first : null;
        _selectedAudio = null;
        _status = 'Ready';
      });

      if (_selectedCamera != null) {
        _probeFormats(_selectedCamera!.index);
      }
    } catch (e) {
      setState(() => _status = 'Error scanning devices: $e');
    }
  }

  // -------------------------------------------------------------------------
  // Format probing
  // -------------------------------------------------------------------------

  Future<void> _probeFormats(int cameraIndex) async {
    setState(() => _status = 'Probing camera $cameraIndex...');

    try {
      final result = await Process.run(
        'ffmpeg',
        [
          '-f', 'avfoundation',
          '-list_formats', 'true',
          '-i', '$cameraIndex',
        ],
        stderrEncoding: const SystemEncoding(),
        stdoutEncoding: const SystemEncoding(),
      );

      final stderr = result.stderr as String;
      final formats = <CameraFormat>[];

      for (final line in stderr.split('\n')) {
        // Match resolution and fps patterns
        final resMatches = RegExp(r'(\d{3,5})x(\d{3,5})').allMatches(line);
        final fpsMatches = RegExp(r'\[(\d+\.?\d*)\s+fps\]').allMatches(line);

        for (final resMatch in resMatches) {
          final w = int.parse(resMatch.group(1)!);
          final h = int.parse(resMatch.group(2)!);
          if (fpsMatches.isNotEmpty) {
            for (final fpsMatch in fpsMatches) {
              final fps = double.parse(fpsMatch.group(1)!);
              formats.add(CameraFormat(w, h, fps));
            }
          } else {
            // Default fps values if not listed
            for (final fps in [30.0, 60.0]) {
              formats.add(CameraFormat(w, h, fps));
            }
          }
        }
      }

      // Deduplicate resolutions
      final resSet = <String>{};
      for (final f in formats) {
        resSet.add('${f.width}x${f.height}');
      }

      final resList = resSet.toList()
        ..sort((a, b) {
          final aw = int.parse(a.split('x')[0]);
          final bw = int.parse(b.split('x')[0]);
          return bw.compareTo(aw); // descending by width
        });

      setState(() {
        _formats = formats;
        _resolutions = resList.isNotEmpty
            ? resList
            : ['3840x2160', '1920x1080', '1280x720', '640x480'];
        _selectedResolution = _resolutions.first;
        _updateFpsForResolution(_selectedResolution!);
        _status = 'Ready';
      });
    } catch (e) {
      setState(() {
        _resolutions = ['3840x2160', '1920x1080', '1280x720', '640x480'];
        _selectedResolution = _resolutions.first;
        _fpsOptions = ['24', '30', '60'];
        _selectedFps = _fpsOptions.last;
        _status = 'Ready (format probe failed)';
      });
    }
  }

  void _updateFpsForResolution(String res) {
    final parts = res.split('x');
    final w = int.parse(parts[0]);
    final h = int.parse(parts[1]);

    final fpsSet = <double>{};
    for (final f in _formats) {
      if (f.width == w && f.height == h) {
        fpsSet.add(f.fps);
      }
    }

    final fpsList = fpsSet.toList()..sort();
    if (fpsList.isNotEmpty) {
      _fpsOptions = fpsList.map((f) {
        return f == f.roundToDouble() && f == f.toInt().toDouble()
            ? f.toInt().toString()
            : f.toString();
      }).toList();
    } else {
      _fpsOptions = ['24', '30', '60'];
    }
    _selectedFps = _fpsOptions.last;
  }

  // -------------------------------------------------------------------------
  // Recording via ffmpeg
  // -------------------------------------------------------------------------

  Future<void> _toggleRecord() async {
    if (_recording) {
      _stopRecording();
    } else {
      _startRecording();
    }
  }

  Future<void> _startRecording() async {
    if (_selectedCamera == null) {
      setState(() => _status = 'No camera selected!');
      return;
    }

    final cam = _selectedCamera!.index;
    final res = _selectedResolution ?? '1920x1080';
    final parts = res.split('x');
    final w = parts[0];
    final h = parts[1];
    final fps = _selectedFps ?? '30';
    final crf = _crf.toInt();
    final codecMap = {
      'h264': 'libx264',
      'hevc': 'libx265',
    };
    final ffmpegCodec = codecMap[_codec] ?? 'libx264';

    // Build output path
    final timestamp =
        DateTime.now().toIso8601String().replaceAll(':', '-').split('.').first;
    final ext = _container;
    final outputPath = p.join(_outputDir, '${_baseName}_$timestamp.$ext');

    // Ensure output directory exists
    await Directory(_outputDir).create(recursive: true);

    // Build ffmpeg args
    final args = <String>[
      '-f', 'avfoundation',
      '-framerate', fps,
      '-video_size', '${w}x$h',
      '-i', '$cam',
    ];

    // Add audio if selected
    if (_selectedAudio != null) {
      args.addAll([
        '-f', 'avfoundation',
        '-i', ':${_selectedAudio!.index}',
      ]);
    }

    args.addAll([
      '-c:v', ffmpegCodec,
      '-crf', '$crf',
      '-preset', 'fast',
    ]);

    if (_selectedAudio != null) {
      args.addAll(['-c:a', 'aac', '-b:a', '128k']);
    }

    args.addAll(['-y', outputPath]);

    try {
      _recordProcess = await Process.start('ffmpeg', args);
      _clipCount++;
      _recordStart = DateTime.now();

      setState(() {
        _recording = true;
        _status = 'Recording #$_clipCount: ${p.basename(outputPath)}';
      });

      // Start timer
      _recordTimer = Timer.periodic(const Duration(milliseconds: 500), (_) {
        if (!_recording || _recordStart == null) return;
        final elapsed = DateTime.now().difference(_recordStart!);
        final h = elapsed.inHours.toString().padLeft(2, '0');
        final m = (elapsed.inMinutes % 60).toString().padLeft(2, '0');
        final s = (elapsed.inSeconds % 60).toString().padLeft(2, '0');
        setState(() => _status = 'REC #$_clipCount  $h:$m:$s');
      });

      // Drain stdout/stderr so process doesn't block
      _recordProcess!.stdout.drain();
      _recordProcess!.stderr.drain();
    } catch (e) {
      setState(() => _status = 'Error starting recording: $e');
    }
  }

  void _stopRecording() {
    _recordTimer?.cancel();
    _recordTimer = null;

    if (_recordProcess != null) {
      // Send 'q' to ffmpeg stdin to quit gracefully
      _recordProcess!.stdin.writeln('q');
      _recordProcess!.stdin.flush();
      // Give it a moment, then kill if needed
      Future.delayed(const Duration(seconds: 3), () {
        _recordProcess?.kill();
        _recordProcess = null;
      });
    }

    final elapsed = _recordStart != null
        ? DateTime.now().difference(_recordStart!).inSeconds
        : 0;

    setState(() {
      _recording = false;
      _status = 'Saved clip #$_clipCount (${elapsed}s)';
    });
    _recordStart = null;
  }

  // -------------------------------------------------------------------------
  // Preview, Thermal, MultiCam via Python CLI
  // -------------------------------------------------------------------------

  Future<void> _openPreview() async {
    if (_selectedCamera == null) {
      setState(() => _status = 'No camera selected!');
      return;
    }

    setState(() => _status = 'Opening preview...');

    final cam = _selectedCamera!.index;
    final res = _selectedResolution ?? '1920x1080';
    final parts = res.split('x');
    final fps = _selectedFps ?? '30';
    final preroll = _preRoll.toInt();
    final crf = _crf.toInt();

    final args = [
      'cli.py', 'preview',
      '-c', '$cam',
      '-W', parts[0], '-H', parts[1],
      '--fps', fps,
      '--codec', _codec,
      '--container', _container,
      '--crf', '$crf',
      '--pre-roll', '$preroll',
      '-o', _outputDir,
      '--base-name', _baseName,
    ];

    if (_selectedAudio != null) {
      args.addAll(['-a', '${_selectedAudio!.index}']);
    }
    if (_burnTimecode) {
      args.add('--overlay');
    }

    try {
      await Process.start('python3', args,
          workingDirectory: _simpleRecorderDir);
      setState(() => _status = 'Preview opened');
    } catch (e) {
      setState(() => _status = 'Error opening preview: $e');
    }
  }

  Future<void> _openThermal() async {
    setState(() => _status = 'Opening thermal preview ($_thermalBackend)...');

    final cam = _selectedCamera?.index ?? 0;
    final args = [
      'cli.py', 'thermal',
      '-m', _thermalBackend,
      '-c', '$cam',
      '--colormap', _colormap,
      '-o', _outputDir,
      '--base-name', _baseName,
    ];

    if (_selectedAudio != null) {
      args.addAll(['-a', '${_selectedAudio!.index}']);
    }

    final rangeMin = _rangeMinCtrl.text.trim();
    final rangeMax = _rangeMaxCtrl.text.trim();
    if (rangeMin.isNotEmpty && rangeMax.isNotEmpty) {
      args.addAll(['--range-min', rangeMin, '--range-max', rangeMax]);
    }

    try {
      await Process.start('python3', args,
          workingDirectory: _simpleRecorderDir);
      setState(() => _status = 'Thermal preview opened');
    } catch (e) {
      setState(() => _status = 'Error opening thermal: $e');
    }
  }

  Future<void> _openMultiCam() async {
    if (_videoDevices.length < 2) {
      setState(() => _status = 'Need 2+ cameras for multi-cam mode');
      return;
    }

    setState(() => _status = 'Opening multi-cam preview...');

    final camIndices = _videoDevices.map((d) => '${d.index}').join(',');
    final args = [
      'cli.py', 'multicam',
      '--cameras', camIndices,
      '-o', _outputDir,
      '--base-name', _baseName,
    ];

    try {
      await Process.start('python3', args,
          workingDirectory: _simpleRecorderDir);
      setState(() => _status = 'Multi-cam preview opened');
    } catch (e) {
      setState(() => _status = 'Error opening multi-cam: $e');
    }
  }

  // -------------------------------------------------------------------------
  // Browse for output folder
  // -------------------------------------------------------------------------

  Future<void> _browseOutputDir() async {
    // Use a simple directory picker dialog
    // file_picker package provides this, but for simplicity we use a
    // process-based approach with osascript on macOS
    try {
      final result = await Process.run('osascript', [
        '-e',
        'set theFolder to POSIX path of (choose folder with prompt "Select output folder")',
      ]);
      final path = (result.stdout as String).trim();
      if (path.isNotEmpty) {
        setState(() => _outputDir = path);
      }
    } catch (_) {
      // Silently ignore if user cancels
    }
  }

  // -------------------------------------------------------------------------
  // UI
  // -------------------------------------------------------------------------

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('flutterRecorder'),
        centerTitle: false,
      ),
      body: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            _buildDevicesSection(),
            const SizedBox(height: 12),
            _buildFormatSection(),
            const SizedBox(height: 12),
            _buildOutputSection(),
            const SizedBox(height: 16),
            _buildActionButtons(),
            const SizedBox(height: 12),
            _buildThermalSection(),
            const SizedBox(height: 12),
            _buildStatusBar(),
          ],
        ),
      ),
    );
  }

  // --- Devices Section ---
  Widget _buildDevicesSection() {
    return _sectionCard(
      title: 'Devices',
      child: Column(
        children: [
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Camera:')),
              Expanded(
                child: DropdownButtonFormField<DeviceInfo>(
                  value: _selectedCamera,
                  isExpanded: true,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: _videoDevices.isEmpty
                      ? [
                          const DropdownMenuItem(
                            value: null,
                            child: Text('(no cameras found)'),
                          )
                        ]
                      : _videoDevices
                          .map((d) => DropdownMenuItem(
                                value: d,
                                child: Text(d.toString(), overflow: TextOverflow.ellipsis),
                              ))
                          .toList(),
                  onChanged: (val) {
                    setState(() => _selectedCamera = val);
                    if (val != null) _probeFormats(val.index);
                  },
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Audio:')),
              Expanded(
                child: DropdownButtonFormField<DeviceInfo?>(
                  value: _selectedAudio,
                  isExpanded: true,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    const DropdownMenuItem<DeviceInfo?>(
                      value: null,
                      child: Text('(none)'),
                    ),
                    ..._audioDevices.map((d) => DropdownMenuItem<DeviceInfo?>(
                          value: d,
                          child: Text(d.toString(), overflow: TextOverflow.ellipsis),
                        )),
                  ],
                  onChanged: (val) => setState(() => _selectedAudio = val),
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.icon(
                onPressed: _refreshDevices,
                icon: const Icon(Icons.refresh, size: 18),
                label: const Text('Refresh'),
              ),
            ],
          ),
        ],
      ),
    );
  }

  // --- Format Section ---
  Widget _buildFormatSection() {
    return _sectionCard(
      title: 'Format',
      child: Column(
        children: [
          // Row 1: Resolution + FPS
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Resolution:')),
              SizedBox(
                width: 150,
                child: DropdownButtonFormField<String>(
                  value: _resolutions.contains(_selectedResolution)
                      ? _selectedResolution
                      : (_resolutions.isNotEmpty ? _resolutions.first : null),
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: _resolutions
                      .map((r) => DropdownMenuItem(value: r, child: Text(r)))
                      .toList(),
                  onChanged: (val) {
                    if (val != null) {
                      setState(() {
                        _selectedResolution = val;
                        _updateFpsForResolution(val);
                      });
                    }
                  },
                ),
              ),
              const SizedBox(width: 16),
              const Text('FPS:'),
              const SizedBox(width: 8),
              SizedBox(
                width: 90,
                child: DropdownButtonFormField<String>(
                  value: _fpsOptions.contains(_selectedFps)
                      ? _selectedFps
                      : (_fpsOptions.isNotEmpty ? _fpsOptions.last : null),
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: _fpsOptions
                      .map((f) => DropdownMenuItem(value: f, child: Text(f)))
                      .toList(),
                  onChanged: (val) => setState(() => _selectedFps = val),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // Row 2: Codec + Container
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Codec:')),
              SizedBox(
                width: 120,
                child: DropdownButtonFormField<String>(
                  value: _codec,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: ['h264', 'hevc']
                      .map((c) => DropdownMenuItem(value: c, child: Text(c)))
                      .toList(),
                  onChanged: (val) => setState(() => _codec = val ?? 'h264'),
                ),
              ),
              const SizedBox(width: 16),
              const Text('Container:'),
              const SizedBox(width: 8),
              SizedBox(
                width: 90,
                child: DropdownButtonFormField<String>(
                  value: _container,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: ['mov', 'mp4']
                      .map((c) => DropdownMenuItem(value: c, child: Text(c)))
                      .toList(),
                  onChanged: (val) =>
                      setState(() => _container = val ?? 'mov'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          // Row 3: CRF slider
          Row(
            children: [
              const SizedBox(width: 80, child: Text('CRF:')),
              Expanded(
                child: Slider(
                  value: _crf,
                  min: 0,
                  max: 51,
                  divisions: 51,
                  label: _crf.toInt().toString(),
                  onChanged: (val) => setState(() => _crf = val),
                ),
              ),
              SizedBox(
                width: 30,
                child: Text(_crf.toInt().toString(),
                    textAlign: TextAlign.center),
              ),
            ],
          ),
          // Row 4: Pre-roll slider
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Pre-roll (s):')),
              Expanded(
                child: Slider(
                  value: _preRoll,
                  min: 0,
                  max: 30,
                  divisions: 30,
                  label: _preRoll.toInt().toString(),
                  onChanged: (val) => setState(() => _preRoll = val),
                ),
              ),
              SizedBox(
                width: 30,
                child: Text(_preRoll.toInt().toString(),
                    textAlign: TextAlign.center),
              ),
            ],
          ),
        ],
      ),
    );
  }

  // --- Output Section ---
  Widget _buildOutputSection() {
    return _sectionCard(
      title: 'Output',
      child: Column(
        children: [
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Folder:')),
              Expanded(
                child: TextFormField(
                  initialValue: _outputDir,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    border: OutlineInputBorder(),
                  ),
                  onChanged: (val) => _outputDir = val,
                ),
              ),
              const SizedBox(width: 8),
              FilledButton.tonal(
                onPressed: _browseOutputDir,
                child: const Text('Browse...'),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Base name:')),
              Expanded(
                child: TextFormField(
                  initialValue: _baseName,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 10),
                    border: OutlineInputBorder(),
                  ),
                  onChanged: (val) => _baseName = val,
                ),
              ),
              const SizedBox(width: 8),
              Row(
                mainAxisSize: MainAxisSize.min,
                children: [
                  Checkbox(
                    value: _burnTimecode,
                    onChanged: (val) =>
                        setState(() => _burnTimecode = val ?? true),
                  ),
                  const Text('Burn timecode'),
                ],
              ),
            ],
          ),
        ],
      ),
    );
  }

  // --- Action Buttons ---
  Widget _buildActionButtons() {
    return Wrap(
      spacing: 8,
      runSpacing: 8,
      children: [
        SizedBox(
          width: 150,
          height: 56,
          child: ElevatedButton.icon(
            onPressed: _toggleRecord,
            icon: Icon(
              _recording ? Icons.stop : Icons.fiber_manual_record,
              color: Colors.white,
            ),
            label: Text(
              _recording ? 'STOP' : 'RECORD',
              style:
                  const TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            style: ElevatedButton.styleFrom(
              backgroundColor:
                  _recording ? const Color(0xFF333333) : const Color(0xFFCC0000),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
        ),
        SizedBox(
          width: 150,
          height: 56,
          child: ElevatedButton.icon(
            onPressed: _openPreview,
            icon: const Icon(Icons.visibility, color: Colors.white),
            label: const Text(
              'PREVIEW',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF336699),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
        ),
        SizedBox(
          width: 150,
          height: 56,
          child: ElevatedButton.icon(
            onPressed: _openThermal,
            icon: const Icon(Icons.thermostat, color: Colors.white),
            label: const Text(
              'THERMAL',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFFCC6600),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
        ),
        SizedBox(
          width: 130,
          height: 56,
          child: ElevatedButton.icon(
            onPressed: _openMultiCam,
            icon: const Icon(Icons.grid_view, color: Colors.white),
            label: const Text(
              'MULTI',
              style: TextStyle(fontWeight: FontWeight.bold, fontSize: 16),
            ),
            style: ElevatedButton.styleFrom(
              backgroundColor: const Color(0xFF669933),
              foregroundColor: Colors.white,
              shape: RoundedRectangleBorder(
                borderRadius: BorderRadius.circular(8),
              ),
            ),
          ),
        ),
      ],
    );
  }

  // --- Thermal Section ---
  Widget _buildThermalSection() {
    return _sectionCard(
      title: 'Thermal Options',
      child: Column(
        children: [
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Backend:')),
              SizedBox(
                width: 130,
                child: DropdownButtonFormField<String>(
                  value: _thermalBackend,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: ['infiray', 'waveshare']
                      .map((b) => DropdownMenuItem(value: b, child: Text(b)))
                      .toList(),
                  onChanged: (val) =>
                      setState(() => _thermalBackend = val ?? 'infiray'),
                ),
              ),
              const SizedBox(width: 16),
              const Text('Colormap:'),
              const SizedBox(width: 8),
              SizedBox(
                width: 130,
                child: DropdownButtonFormField<String>(
                  value: _colormap,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 12, vertical: 8),
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    'inferno', 'jet', 'hot', 'turbo', 'magma',
                    'rainbow', 'bone', 'white_hot', 'black_hot',
                  ]
                      .map((c) => DropdownMenuItem(value: c, child: Text(c)))
                      .toList(),
                  onChanged: (val) =>
                      setState(() => _colormap = val ?? 'inferno'),
                ),
              ),
            ],
          ),
          const SizedBox(height: 8),
          Row(
            children: [
              const SizedBox(width: 80, child: Text('Range lock:')),
              SizedBox(
                width: 70,
                child: TextFormField(
                  controller: _rangeMinCtrl,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 8, vertical: 8),
                    border: OutlineInputBorder(),
                    hintText: 'min',
                  ),
                  keyboardType: TextInputType.number,
                ),
              ),
              const Padding(
                padding: EdgeInsets.symmetric(horizontal: 8),
                child: Text('-'),
              ),
              SizedBox(
                width: 70,
                child: TextFormField(
                  controller: _rangeMaxCtrl,
                  decoration: const InputDecoration(
                    isDense: true,
                    contentPadding:
                        EdgeInsets.symmetric(horizontal: 8, vertical: 8),
                    border: OutlineInputBorder(),
                    hintText: 'max',
                  ),
                  keyboardType: TextInputType.number,
                ),
              ),
              const SizedBox(width: 8),
              Text('C (blank=auto)',
                  style: TextStyle(color: Colors.grey.shade600, fontSize: 12)),
            ],
          ),
        ],
      ),
    );
  }

  // --- Status Bar ---
  Widget _buildStatusBar() {
    final isRecording = _status.contains('REC');
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
      decoration: BoxDecoration(
        color: isRecording ? Colors.red.shade50 : Colors.grey.shade100,
        borderRadius: BorderRadius.circular(6),
        border: Border.all(
          color: isRecording ? Colors.red.shade200 : Colors.grey.shade300,
        ),
      ),
      child: Row(
        children: [
          if (isRecording)
            Container(
              width: 10,
              height: 10,
              margin: const EdgeInsets.only(right: 8),
              decoration: const BoxDecoration(
                color: Colors.red,
                shape: BoxShape.circle,
              ),
            ),
          Text(_status, style: const TextStyle(fontSize: 13)),
        ],
      ),
    );
  }

  // --- Section card helper ---
  Widget _sectionCard({required String title, required Widget child}) {
    return Card(
      elevation: 0,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(8),
        side: BorderSide(color: Colors.grey.shade300),
      ),
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(title,
                style: const TextStyle(
                    fontWeight: FontWeight.bold, fontSize: 14)),
            const SizedBox(height: 8),
            child,
          ],
        ),
      ),
    );
  }
}
