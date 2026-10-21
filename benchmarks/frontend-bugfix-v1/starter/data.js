// Fixture data for the schedule. Doctor ids are numbers; the <select> gives strings.
window.CLINIC = {
  doctors: [
    { id: 1, name: "Dr. Ana Pérez" },
    { id: 2, name: "Dr. Bruno Silva" },
    { id: 3, name: "Dr. Carla Gómez" }
  ],
  appointments: [
    { id: "a1", time: "09:00", patient: "Lucía Fernández", doctorId: 1 },
    { id: "a2", time: "09:30", patient: "Martín Rodríguez", doctorId: 2 },
    { id: "a3", time: "10:00", patient: "Sofía López", doctorId: 1 },
    { id: "a4", time: "10:30", patient: "Diego Martínez", doctorId: 3 },
    { id: "a5", time: "11:00", patient: "<b onmouseover=\"document.title='pwned'\">Valentina Ruiz</b>", doctorId: 2 }
  ]
};
