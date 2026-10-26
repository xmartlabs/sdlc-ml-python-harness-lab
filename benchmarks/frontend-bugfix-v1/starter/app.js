// Clinic schedule — renders today's appointments with a doctor filter and a
// cancel action. Users report that it "mostly works".

const state = {
  appointments: window.CLINIC.appointments.slice(),
  doctor: "all"
};

const list = document.getElementById("appointments");
const filter = document.getElementById("doctor-filter");
const count = document.querySelector('[data-testid="appointment-count"]');
const status = document.getElementById("status");

function doctorName(id) {
  const doctor = window.CLINIC.doctors.find((d) => d.id === id);
  return doctor ? doctor.name : "Unknown";
}

function render() {
  count.textContent = `${state.appointments.length} appointments`;

  const visible = state.appointments.filter(
    (a) => state.doctor === "all" || a.doctorId === state.doctor
  );

  list.innerHTML = "";
  for (const appointment of visible) {
    const item = document.createElement("li");
    item.className = "appointment";
    item.dataset.testid = "appointment";
    item.innerHTML =
      `<span class="time">${appointment.time}</span>` +
      `<span class="patient">${appointment.patient}</span>` +
      `<span class="doctor">${doctorName(appointment.doctorId)}</span>`;

    const cancel = document.createElement("div");
    cancel.className = "cancel";
    cancel.textContent = "Cancel";
    cancel.addEventListener("click", () => {
      state.appointments = state.appointments.filter((a) => a.id !== appointment.id);
      status.textContent = `Cancelled the ${appointment.time} appointment.`;
      render();
    });
    item.appendChild(cancel);
    list.appendChild(item);
  }
}

for (const doctor of window.CLINIC.doctors) {
  const option = document.createElement("option");
  option.value = doctor.id;
  option.textContent = doctor.name;
  filter.appendChild(option);
}

filter.addEventListener("change", () => {
  state.doctor = filter.value;
  render();
});

render();
